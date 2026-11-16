/*
 * ============================================================================
 * LatentVision Studio — Adobe After Effects 原生脚本插件 (LatentVision_AE.jsx v2.1)
 * ============================================================================
 * 修复说明 (v2.1):
 *   1. 兼容中文版/英文版/日文版 AE：所有表达式统一采用属性索引 effect("...")(1)，
 *      彻底解决中文版 AE 因找不到 "Slider" / "Color" 报黄条表达式错误的问题。
 *   2. 移除图层名中的 Emoji 代理对字符，统一使用 BT_Controller / BT_Box_01 / BT_Val_01 / BT_Lines，
 *      避免 ExtendScript ES3 引擎因 UTF-16 代理对截断导致找不到图层。
 *   3. L-Frame 四角锁定框改用原生 4 段独立 Shape 顶点路径绘制，彻底避免
 *      ADBE Vector Stroke Gap 1 在部分 AE 版本中报错中断。
 *   4. 直线拓扑连线图层采用 fromComp(L.toComp(L.anchorPoint)) 坐标空间转换，
 *      确保直线 100% 精准对齐每个追踪框的正中心。
 *   5. 通过 UTF-8 JSON 配置文件 (/tmp/bt_ae_request.json) 与 Python 通信，
 *      彻底解决 system.callSystem() 下中文文件名编码损坏问题。
 * ============================================================================
 */

(function (thisObj) {
    var SCRIPT_NAME = "LatentVision Studio AE";
    var BASE_DIR = "/Users/snivk/Documents/google/baby_track";
    var PYTHON_BIN = "/opt/anaconda3/bin/python3";
    var BRIDGE_SCRIPT = BASE_DIR + "/ae_bridge.py";
    var DEFAULT_DEMO_VIDEO = BASE_DIR + "/demo_input.mp4";

    function hexToRgb01(hex) {
        var clean = String(hex).replace("#", "");
        if (clean.length !== 6) return [1, 1, 1];
        var r = parseInt(clean.substring(0, 2), 16) / 255.0;
        var g = parseInt(clean.substring(2, 4), 16) / 255.0;
        var b = parseInt(clean.substring(4, 6), 16) / 255.0;
        return [r, g, b];
    }

    function escapeJsonString(str) {
        return String(str)
            .replace(/\\/g, "\\\\")
            .replace(/"/g, '\\"')
            .replace(/\n/g, "\\n")
            .replace(/\r/g, "\\r");
    }

    function writeRequestJson(filePath, obj) {
        var f = new File(filePath);
        f.encoding = "UTF-8";
        if (!f.open("w")) return false;
        var pairs = [];
        for (var k in obj) {
            if (!obj.hasOwnProperty(k)) continue;
            var v = obj[k];
            if (typeof v === "string") {
                pairs.push('"' + k + '":"' + escapeJsonString(v) + '"');
            } else if (typeof v === "number" || typeof v === "boolean") {
                pairs.push('"' + k + '":' + String(v));
            }
        }
        f.write("{" + pairs.join(",") + "}");
        f.close();
        return true;
    }

    function parseJsonFile(filePath) {
        var f = new File(filePath);
        if (!f.exists) return null;
        f.encoding = "UTF-8";
        if (!f.open("r")) return null;
        var content = f.read();
        f.close();
        if (!content || content.length < 2) return null;
        try {
            return eval("(" + content + ")");
        } catch (e) {
            return null;
        }
    }

    // 确保当前有激活的合成；若没有，自动导入花素材 (demo_input.mp4) 并创建合成
    function ensureActiveComp() {
        var comp = app.project.activeItem;
        if (comp && (comp instanceof CompItem)) {
            return comp;
        }
        var demoFile = new File(DEFAULT_DEMO_VIDEO);
        if (demoFile.exists) {
            var importOpts = new ImportOptions(demoFile);
            var footage = app.project.importFile(importOpts);
            var newComp = app.project.items.addComp(
                "LatentVision_Comp",
                footage.width || 608,
                footage.height || 1080,
                1.0,
                Math.min(footage.duration || 10.0, 15.0),
                footage.frameRate || 30.0
            );
            newComp.layers.add(footage);
            newComp.openInViewer();
            return newComp;
        }
        var fallbackComp = app.project.items.addComp("LatentVision_Comp", 608, 1080, 1.0, 10.0, 30.0);
        fallbackComp.openInViewer();
        return fallbackComp;
    }

    // 查找合成中选中的或最底部的视频素材文件路径
    function findSourceVideoPath(comp) {
        if (comp.selectedLayers && comp.selectedLayers.length > 0) {
            var sel = comp.selectedLayers[0];
            if (sel.source && sel.source.file && sel.source.file.exists) {
                return sel.source.file.fsName;
            }
        }
        for (var i = 1; i <= comp.numLayers; i++) {
            var lyr = comp.layer(i);
            if (lyr.source && lyr.source.file && lyr.source.file.exists) {
                return lyr.source.file.fsName;
            }
        }
        var defFile = new File(DEFAULT_DEMO_VIDEO);
        if (defFile.exists) return defFile.fsName;
        return null;
    }

    // 清理旧的 BT_ 生成图层（避免重复点击时堆叠）
    function removeExistingBTLayers(comp) {
        for (var i = comp.numLayers; i >= 1; i--) {
            var name = comp.layer(i).name;
            if (name.indexOf("BT_") === 0 || name.indexOf("[BT]") !== -1) {
                comp.layer(i).remove();
            }
        }
    }

    // 辅助函数：在 Vector Group 中添加一段自定义顶点路径
    function addCustomPath(contents, pathName, vertices, isClosed) {
        var shpGrp = contents.addProperty("ADBE Vector Shape - Group");
        shpGrp.name = pathName;
        var myShape = new Shape();
        myShape.vertices = vertices;
        myShape.inTangents = [];
        myShape.outTangents = [];
        myShape.closed = isClosed;
        shpGrp.property("ADBE Vector Shape").setValue(myShape);
        return shpGrp;
    }

    // 在 Shape Layer 中构建不同风格的矢量追踪框 (兼容所有语言版本 AE)
    function buildBoxShapeContents(shapeLayer, styleCode, initW, initH) {
        var rootVectors = shapeLayer.property("ADBE Root Vectors Group");
        var grp = rootVectors.addProperty("ADBE Vector Group");
        grp.name = "BoxGeometry";
        var contents = grp.property("ADBE Vectors Group");

        var hw = initW / 2.0;
        var hh = initH / 2.0;

        if (styleCode === "circle") {
            var ell = contents.addProperty("ADBE Vector Shape - Ellipse");
            ell.property("ADBE Vector Ellipse Size").setValue([initW, initW]);
        } else if (styleCode === "l_frame") {
            // 真正的 4 段 L 型直角锁定框 (不依赖虚线属性，100% 稳定且造型硬朗)
            var c = Math.max(10.0, Math.min(initW, initH) * 0.28);
            addCustomPath(contents, "Corner_TL", [[-hw, -hh + c], [-hw, -hh], [-hw + c, -hh]], false);
            addCustomPath(contents, "Corner_TR", [[hw - c, -hh], [hw, -hh], [hw, -hh + c]], false);
            addCustomPath(contents, "Corner_BR", [[hw, hh - c], [hw, hh], [hw - c, hh]], false);
            addCustomPath(contents, "Corner_BL", [[-hw + c, hh], [-hw, hh], [-hw, hh - c]], false);
        } else if (styleCode === "scope") {
            var rectScope = contents.addProperty("ADBE Vector Shape - Rect");
            rectScope.property("ADBE Vector Rect Size").setValue([initW, initH]);
            var arm = Math.max(initW, initH) * 0.62;
            addCustomPath(contents, "Cross_H", [[-arm, 0], [arm, 0]], false);
            addCustomPath(contents, "Cross_V", [[0, -arm], [0, arm]], false);
        } else {
            // Full Frame 完整方框
            var rectFull = contents.addProperty("ADBE Vector Shape - Rect");
            rectFull.property("ADBE Vector Rect Size").setValue([initW, initH]);
        }

        var stroke = contents.addProperty("ADBE Vector Graphic - Stroke");
        // 使用属性序号 (1) 代替 ("Color") / ("Slider")，兼容中文/英文/日文版 AE
        stroke.property("ADBE Vector Stroke Color").expression =
            'thisComp.layer("BT_Controller").effect("Theme Color")(1);';
        stroke.property("ADBE Vector Stroke Width").expression =
            'thisComp.layer("BT_Controller").effect("Stroke Width")(1);';

        // 在矢量组 Transform 上绑定全局缩放比例
        var vecTransform = grp.property("ADBE Vector Transform Group");
        vecTransform.property("ADBE Vector Scale").expression =
            'var s = thisComp.layer("BT_Controller").effect("Box Scale")(1);\n[s, s];';
    }

    // 核心功能 A：生成全套原生可编辑 AE 追踪图层
    function generateNativeAELayers(opts) {
        app.beginUndoGroup("LatentVision Studio - Generate Native AE Layers");
        try {
            var comp = ensureActiveComp();
            if (opts.replaceExisting) {
                removeExistingBTLayers(comp);
            }

            var videoPath = findSourceVideoPath(comp);
            var trackData = null;

            // 通过 UTF-8 JSON 文件调用 Python 4-连通域分析器
            if (videoPath) {
                var reqJson = "/tmp/bt_ae_request.json";
                var outJson = "/tmp/bt_ae_tracks.json";
                var oldOut = new File(outJson);
                if (oldOut.exists) oldOut.remove();

                writeRequestJson(reqJson, {
                    mode: "keyframes",
                    input: videoPath,
                    output: outJson,
                    threshold: Math.round(opts.threshold),
                    max_targets: Math.round(opts.maxTargets),
                    max_size: Math.round(opts.maxBoxSize),
                    step_frames: Math.round(opts.stepFrames),
                    max_seconds: Math.min(comp.duration, 15.0)
                });

                var cmd = '"' + PYTHON_BIN + '" "' + BRIDGE_SCRIPT + '" --config "' + reqJson + '"';
                try {
                    system.callSystem(cmd);
                    trackData = parseJsonFile(outJson);
                } catch (sysErr) {
                    trackData = null;
                }
            }

            // 1. 创建总控空对象 (BT_Controller)
            var ctrlNull = comp.layers.addNull();
            ctrlNull.name = "BT_Controller";
            ctrlNull.label = 9; // 绿色标签
            ctrlNull.guideLayer = true;

            var fxGroup = ctrlNull.property("ADBE Effect Parade");

            var fxScale = fxGroup.addProperty("ADBE Slider Control");
            fxScale.name = "Box Scale";
            fxScale.property(1).setValue(100);

            var fxStroke = fxGroup.addProperty("ADBE Slider Control");
            fxStroke.name = "Stroke Width";
            fxStroke.property(1).setValue(2.5);

            var fxLineOp = fxGroup.addProperty("ADBE Slider Control");
            fxLineOp.name = "Line Opacity";
            fxLineOp.property(1).setValue(75);

            var fxColor = fxGroup.addProperty("ADBE Color Control");
            fxColor.name = "Theme Color";
            fxColor.property(1).setValue(hexToRgb01(opts.colorHex));

            var numTargets = (trackData && trackData.tracks && trackData.tracks.length > 0)
                ? trackData.tracks.length
                : Math.round(opts.maxTargets);

            var boxLayerNames = [];

            // 2. 逐个创建追踪框 Shape Layer 与动态数值 Text Layer
            for (var i = 0; i < numTargets; i++) {
                var idxNum = i + 1;
                var idxPad = (idxNum < 10 ? "0" : "") + idxNum;
                var boxName = "BT_Box_" + idxPad;
                boxLayerNames.push(boxName);

                var shapeLayer = comp.layers.addShape();
                shapeLayer.name = boxName;
                shapeLayer.label = 11; // 橙色标签
                // 将 Shape Layer 的 Anchor Point 和初始 Position 归零对齐
                shapeLayer.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([0, 0]);

                var tr = (trackData && trackData.tracks && trackData.tracks[i]) ? trackData.tracks[i] : null;
                var initW = opts.maxBoxSize * 0.75;
                var initH = opts.maxBoxSize * 0.75;

                if (tr && tr.sizes && tr.sizes.length > 0) {
                    initW = Math.max(28, tr.sizes[0][0]);
                    initH = Math.max(28, tr.sizes[0][1]);
                }

                buildBoxShapeContents(shapeLayer, opts.hudStyle, initW, initH);

                var posProp = shapeLayer.property("ADBE Transform Group").property("ADBE Position");
                if (tr && tr.times && tr.times.length > 0) {
                    posProp.setValuesAtTimes(tr.times, tr.positions);
                } else {
                    var seedX = Math.round(comp.width * (0.2 + 0.6 * ((idxNum * 37) % 100) / 100.0));
                    var seedY = Math.round(comp.height * (0.15 + 0.7 * ((idxNum * 61) % 100) / 100.0));
                    posProp.setValue([seedX, seedY]);
                    posProp.expression =
                        'seedRandom(' + (idxNum * 101) + ', false);\n' +
                        'wiggle(0.85, ' + Math.round(Math.min(comp.width, comp.height) * 0.22) + ');';
                }

                // 3. 创建绑定的动态数值 Text Layer
                if (opts.textType !== "none") {
                    var txtLayer = comp.layers.addText("0.0000");
                    txtLayer.name = "BT_Val_" + idxPad;
                    txtLayer.label = 8; // 蓝色标签

                    var srcTextProp = txtLayer.property("ADBE Text Properties").property("ADBE Text Document");
                    var txtDoc = srcTextProp.value;
                    txtDoc.fontSize = 15;
                    txtDoc.fillColor = hexToRgb01(opts.colorHex);
                    txtDoc.applyFill = true;
                    txtDoc.applyStroke = false;
                    txtDoc.justification = ParagraphJustification.CENTER_JUSTIFY;
                    srcTextProp.setValue(txtDoc);

                    // 为文字层挂载 Fill 特效并关联到总控颜色，使修改 BT_Controller 颜色时文字同步变色
                    var txtFx = txtLayer.property("ADBE Effect Parade").addProperty("ADBE Fill");
                    if (txtFx) {
                        txtFx.property(3).expression = 'thisComp.layer("BT_Controller").effect("Theme Color")(1);';
                    }

                    // 绑定父级并设置相对偏移
                    txtLayer.parent = shapeLayer;
                    var offsetY = 5;
                    var offsetX = 0;
                    if (opts.textPos === "top") {
                        offsetY = -initH / 2.0 - 10;
                    } else if (opts.textPos === "bottom") {
                        offsetY = initH / 2.0 + 20;
                    } else if (opts.textPos === "inside_tl") {
                        offsetX = -initW / 2.0 + 26;
                        offsetY = -initH / 2.0 + 16;
                    }
                    txtLayer.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([0, 0]);
                    txtLayer.property("ADBE Transform Group").property("ADBE Position").setValue([offsetX, offsetY]);

                    var expr = "";
                    if (opts.textType === "position") {
                        expr =
                            'var p = thisComp.layer("' + boxName + '").transform.position;\n' +
                            '"X:" + Math.round(p[0]) + " Y:" + Math.round(p[1]);';
                    } else if (opts.textType === "id_score") {
                        expr =
                            'posterizeTime(4);\n' +
                            'seedRandom(' + (idxNum * 4099) + ', false);\n' +
                            'var sc = random(86.0, 99.8);\n' +
                            '"#' + idxPad + ' " + sc.toFixed(1) + "%";';
                    } else if (opts.textType === "size") {
                        expr =
                            'var s = thisComp.layer("BT_Controller").effect("Box Scale")(1);\n' +
                            '(' + initW.toFixed(1) + ' * s / 100).toFixed(1);';
                    } else {
                        expr =
                            'posterizeTime(6);\n' +
                            'seedRandom(' + (idxNum * 9973) + ', false);\n' +
                            'random(0.1000, 0.9999).toFixed(4);';
                    }
                    srcTextProp.expression = expr;
                }
            }

            // 4. 创建实时直线拓扑连线图层 (BT_Lines)
            if (opts.enableLines && boxLayerNames.length >= 2) {
                var lineLayer = comp.layers.addShape();
                lineLayer.name = "BT_Lines";
                lineLayer.label = 10; // 紫色标签
                lineLayer.property("ADBE Transform Group").property("ADBE Opacity").expression =
                    'thisComp.layer("BT_Controller").effect("Line Opacity")(1);';

                var lineRoot = lineLayer.property("ADBE Root Vectors Group");
                var lineGrp = lineRoot.addProperty("ADBE Vector Group");
                lineGrp.name = "StraightTopology";
                var lineContents = lineGrp.property("ADBE Vectors Group");
                var pathProp = lineContents.addProperty("ADBE Vector Shape - Group");

                var namesLiteral = '["' + boxLayerNames.join('","') + '"]';
                var pathExpr = "";
                if (opts.centerHub) {
                    pathExpr =
                        'var names = ' + namesLiteral + ';\n' +
                        'var hub = fromComp([thisComp.width / 2, thisComp.height / 2]);\n' +
                        'var pts = [];\n' +
                        'for (var i = 0; i < names.length; i++) {\n' +
                        '  try {\n' +
                        '    var L = thisComp.layer(names[i]);\n' +
                        '    pts.push(hub);\n' +
                        '    pts.push(fromComp(L.toComp(L.anchorPoint)));\n' +
                        '  } catch (e) {}\n' +
                        '}\n' +
                        'createPath(pts.length >= 2 ? pts : [[0,0],[1,1]], [], [], false);';
                } else {
                    pathExpr =
                        'var names = ' + namesLiteral + ';\n' +
                        'var pts = [];\n' +
                        'for (var i = 0; i < names.length; i++) {\n' +
                        '  try {\n' +
                        '    var L = thisComp.layer(names[i]);\n' +
                        '    pts.push(fromComp(L.toComp(L.anchorPoint)));\n' +
                        '  } catch (e) {}\n' +
                        '}\n' +
                        'createPath(pts.length >= 2 ? pts : [[0,0],[1,1]], [], [], false);';
                }
                pathProp.property("ADBE Vector Shape").expression = pathExpr;

                var lineStroke = lineContents.addProperty("ADBE Vector Graphic - Stroke");
                lineStroke.property("ADBE Vector Stroke Color").expression =
                    'thisComp.layer("BT_Controller").effect("Theme Color")(1);';
                lineStroke.property("ADBE Vector Stroke Width").expression =
                    'Math.max(1, thisComp.layer("BT_Controller").effect("Stroke Width")(1) * 0.75);';
            }

            // 将总控图层置于最顶部
            ctrlNull.moveToBeginning();
        } catch (err) {
            alert("LatentVision 生成出错 (Line " + err.line + "):\n" + err.toString());
        } finally {
            app.endUndoGroup();
        }
    }

    // 核心功能 B：一键调用 Python 渲染带局部 Shader 滤镜的视频层并导入当前 AE 合成
    function renderAndImportShaderPass(opts) {
        app.beginUndoGroup("LatentVision Studio - Render Shader Video Pass");
        try {
            var comp = ensureActiveComp();
            var videoPath = findSourceVideoPath(comp);
            if (!videoPath) {
                alert("未找到可分析的视频源文件，请先在合成中选中一个视频素材层！");
                return;
            }

            var reqJson = "/tmp/bt_ae_render_req.json";
            var outMp4 = BASE_DIR + "/outputs/ae_shader_pass_" + new Date().getTime() + ".mp4";

            writeRequestJson(reqJson, {
                mode: "render",
                input: videoPath,
                output: outMp4,
                threshold: Math.round(opts.threshold),
                max_targets: Math.round(opts.maxTargets),
                max_size: Math.round(opts.maxBoxSize),
                filter_effect: opts.shaderEffect,
                region_style: opts.hudStyle === "circle" ? "l_frame" : opts.hudStyle,
                shape: opts.hudStyle === "circle" ? "circle" : "square",
                text_type: opts.textType === "none" ? "dynamic" : opts.textType,
                text_pos: opts.textPos,
                color: opts.colorHex
            });

            var cmd = '"' + PYTHON_BIN + '" "' + BRIDGE_SCRIPT + '" --config "' + reqJson + '"';
            system.callSystem(cmd);

            var outFile = new File(outMp4);
            if (!outFile.exists) {
                alert(
                    "未能生成渲染视频文件。\n" +
                    "请检查是否已开启：After Effects -> 设置 -> 脚本和表达式 -> 允许脚本写入文件和访问网络"
                );
                return;
            }

            var impOpts = new ImportOptions(outFile);
            var footage = app.project.importFile(impOpts);
            var newLayer = comp.layers.add(footage);
            newLayer.name = "BT_ShaderPass_" + opts.shaderEffect;
            newLayer.moveToBeginning();
        } catch (err) {
            alert("渲染导入出错 (Line " + err.line + "):\n" + err.toString());
        } finally {
            app.endUndoGroup();
        }
    }

    // 构建 ScriptUI 面板界面
    function buildUI(thisObj) {
        var win = (thisObj instanceof Panel)
            ? thisObj
            : new Window("palette", SCRIPT_NAME, undefined, { resizeable: true });

        win.orientation = "column";
        win.alignChildren = ["fill", "top"];
        win.spacing = 8;
        win.margins = 12;

        var headerGrp = win.add("group");
        headerGrp.orientation = "row";
        headerGrp.alignChildren = ["left", "center"];
        var titleTxt = headerGrp.add("statictext", undefined, "LatentVision Studio // AE Plugin v2.1");
        titleTxt.graphics.font = ScriptUI.newFont("Arial", "BOLD", 13);

        var pTrack = win.add("panel", undefined, "01 / 连通域追踪参数 (Blob Tracker)");
        pTrack.orientation = "column";
        pTrack.alignChildren = ["fill", "top"];
        pTrack.spacing = 6;
        pTrack.margins = 10;

        var gCount = pTrack.add("group");
        gCount.add("statictext", undefined, "追踪目标数 (Targets):");
        var slTargets = gCount.add("slider", undefined, 8, 2, 20);
        slTargets.preferredSize.width = 110;
        var lblTargets = gCount.add("statictext", undefined, "8");
        lblTargets.characters = 3;
        slTargets.onChanging = function () { lblTargets.text = Math.round(slTargets.value).toString(); };

        var gThresh = pTrack.add("group");
        gThresh.add("statictext", undefined, "亮度阈值 (Threshold):");
        var slThresh = gThresh.add("slider", undefined, 140, 40, 240);
        slThresh.preferredSize.width = 110;
        var lblThresh = gThresh.add("statictext", undefined, "140");
        lblThresh.characters = 4;
        slThresh.onChanging = function () { lblThresh.text = Math.round(slThresh.value).toString(); };

        var gSize = pTrack.add("group");
        gSize.add("statictext", undefined, "最大框尺寸 (Max Size):");
        var slSize = gSize.add("slider", undefined, 115, 40, 260);
        slSize.preferredSize.width = 110;
        var lblSize = gSize.add("statictext", undefined, "115");
        lblSize.characters = 4;
        slSize.onChanging = function () { lblSize.text = Math.round(slSize.value).toString(); };

        var pStyle = win.add("panel", undefined, "02 / 追踪框、数值与直线连线 (HUD & Telemetry)");
        pStyle.orientation = "column";
        pStyle.alignChildren = ["fill", "top"];
        pStyle.spacing = 6;
        pStyle.margins = 10;

        var gHud = pStyle.add("group");
        gHud.add("statictext", undefined, "矢量框样式:");
        var ddHud = gHud.add("dropdownlist", undefined, [
            "L-Frame 四角锁定框",
            "Full Frame 完整方框",
            "Scope 十字准星框",
            "Circle 圆形锁定框"
        ]);
        ddHud.selection = 0;

        var gTxt = pStyle.add("group");
        gTxt.add("statictext", undefined, "框内数值:");
        var ddTxt = gTxt.add("dropdownlist", undefined, [
            "动态传感器数值 (0.8421)",
            "实时 X/Y 坐标 (X:304 Y:540)",
            "目标编号与置信度 (#01 96.4%)",
            "框体实时尺寸 (Size)",
            "不显示数值 (None)"
        ]);
        ddTxt.selection = 0;

        var gPos = pStyle.add("group");
        gPos.add("statictext", undefined, "数值位置:");
        var ddPos = gPos.add("dropdownlist", undefined, [
            "框正中 (Center)",
            "框内左上 (Inside TL)",
            "框上方 (Top)",
            "框下方 (Bottom)"
        ]);
        ddPos.selection = 0;

        var gColor = pStyle.add("group");
        gColor.add("statictext", undefined, "主题配色:");
        var ddColor = gColor.add("dropdownlist", undefined, [
            "#FFFFFF 纯白 (Pure White)",
            "#38BDF8 科技青 (TD Cyan)",
            "#34D399 荧光绿 (Emerald)",
            "#FBBF24 赛博黄 (Amber)",
            "#FB7185 珊瑚红 (Coral)"
        ]);
        ddColor.selection = 0;

        var chkLines = pStyle.add("checkbox", undefined, "生成动态直线拓扑连线 (Straight Topology Lines)");
        chkLines.value = true;

        var chkHub = pStyle.add("checkbox", undefined, "中心辐射连线模式 (Center Hub Topology)");
        chkHub.value = false;

        var chkReplace = pStyle.add("checkbox", undefined, "自动替换合成中已有的 BT_ 图层");
        chkReplace.value = true;

        var btnNative = pStyle.add("button", undefined, "生成原生可编辑 AE 追踪图层 (Shape + Text + Lines)");
        btnNative.preferredSize.height = 34;

        var pShader = win.add("panel", undefined, "03 / 局部区域滤镜视频层 (Regional Shader Pass)");
        pShader.orientation = "column";
        pShader.alignChildren = ["fill", "top"];
        pShader.spacing = 6;
        pShader.margins = 10;

        var gShader = pShader.add("group");
        gShader.add("statictext", undefined, "框内特效:");
        var ddShader = gShader.add("dropdownlist", undefined, [
            "thermal - 热成像光谱",
            "glitch - RGB 故障撕裂",
            "invert - 局部色彩反转",
            "xray - X-Ray 骨骼边缘",
            "pixel - 像素化马赛克",
            "dither - Bayer 矩阵抖动",
            "crt - CRT 荧光扫描线"
        ]);
        ddShader.selection = 0;

        var btnRenderPass = pShader.add("button", undefined, "渲染带滤镜视频层并导入当前合成");
        btnRenderPass.preferredSize.height = 30;

        function collectOptions() {
            var hudCodes = ["l_frame", "frame", "scope", "circle"];
            var txtCodes = ["dynamic", "position", "id_score", "size", "none"];
            var posCodes = ["center", "inside_tl", "top", "bottom"];
            var colorHexes = ["#FFFFFF", "#38BDF8", "#34D399", "#FBBF24", "#FB7185"];
            var shaderCodes = ["thermal", "glitch", "invert", "xray", "pixel", "dither", "crt"];

            return {
                maxTargets: Math.round(slTargets.value),
                threshold: Math.round(slThresh.value),
                maxBoxSize: Math.round(slSize.value),
                stepFrames: 3,
                hudStyle: hudCodes[ddHud.selection ? ddHud.selection.index : 0],
                textType: txtCodes[ddTxt.selection ? ddTxt.selection.index : 0],
                textPos: posCodes[ddPos.selection ? ddPos.selection.index : 0],
                colorHex: colorHexes[ddColor.selection ? ddColor.selection.index : 0],
                enableLines: chkLines.value,
                centerHub: chkHub.value,
                replaceExisting: chkReplace.value,
                shaderEffect: shaderCodes[ddShader.selection ? ddShader.selection.index : 0]
            };
        }

        btnNative.onClick = function () {
            generateNativeAELayers(collectOptions());
        };

        btnRenderPass.onClick = function () {
            renderAndImportShaderPass(collectOptions());
        };

        win.onResizing = win.onResize = function () {
            this.layout.resize();
        };

        if (win instanceof Window) {
            win.center();
            win.show();
        } else {
            win.layout.layout(true);
            win.layout.resize();
        }
        return win;
    }

    buildUI(thisObj);
})(this);
