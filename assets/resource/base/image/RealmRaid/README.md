# 个人结界突破图像

`UnchallengedCard.png` 由本项目保存的1280×720无损游戏截图自行裁剪：完整317×120卡片，保留边框、五个圆圈外缘和右上角空白；绿色蒙版屏蔽头像、昵称、勋章内部。Pipeline 使用原生 TemplateMatch，昵称与角标另在所选卡片的动态 ROI 核对。没有从外部仓库复制图像或源码。

选卡思路参考 [OnmyojiAutoScript / RealmRaid](https://github.com/runhey/OnmyojiAutoScript/blob/88c3a936246fc95ad1a48feecc0acd71d3f5508a/tasks/RealmRaid/script_task.py) 的 `order_medal`、`find_one`、`partition` 和失败标识排除逻辑，以及同版本 `tasks/RealmRaid/assets.py`、`module/atom/image_grid.py`、`module/atom/image.py`。其突破复用 `tasks/Component/GeneralBattle/general_battle.py`，并通过 `battle_wait.py` 的策略事件循环处理战斗。

本项目采用 MaaFramework 原生模板匹配与 `roi: RealmRaidChoose` 保存当前目标，不增加突破专用 Python 模块。与参考项目不同，本任务不按勋章数量排序，也不在开始战斗时计数；只在结算返回后核对所选卡片的攻破印记才计数。真实战败后的处理对应 OAS `WhenAttackFail`：默认跳过带失败角标的卡片继续，可选战败后刷新（确认框“刷新后攻破记录进度将重置，确定吗?”，刷新冷却读不到按钮时结束）或战败后结束本轮。

## 战斗返回按钮

`BattleExit_1.png`～`BattleExit_5.png` 原样来自 [MaaYYs](https://github.com/TanyaShue/MaaYYs/blob/3f9a969/resource_pack/base/pipeline/战斗/自动结界.json) `突破_重新挑战-模板匹配` 节点引用的五个模板，采用 MIT License，许可证保存在同目录 `MaaYYs-LICENSE.txt`：

| 本项目文件 | 原文件 |
| --- | --- |
| `BattleExit_1.png` | `image/战斗/自动结界/突破_重新挑战-模板匹配_1.png` |
| `BattleExit_2.png` | `image/自动结界/突破_重新挑战-模板匹配_1.png` |
| `BattleExit_3.png` | `image/自动结界/突破_重新挑战-模板匹配_2.png` |
| `BattleExit_4.png` | `image/通用图标/战斗_返回.png` |
| `BattleExit_5.png` | `image/通用图标/战斗_返回_原版.png` |

ROI 同样沿用 MaaYYs 的 `[0,4,77,75]`，与 OnmyojiAutoScript `I_EXIT (14,12,43,41)` 是同一按钮。本地真实御魂战斗截图命中 `[5,6,59,59]`。

`BattleExit_6.png` 由本项目1280×720个人突破实战截图自行裁剪按钮内圈28×28（原图 `[22,20,28,28]`），不含背景。当前版本普通突破战斗中它得分1.0，MaaYYs 模板仅约0.80（阈值边缘，曾导致退出晚约4秒）；准备阶段、个人突破页和结算页均低于0.41。实测当前版本准备阶段没有返回按钮，开打后才出现；点击后约1.3秒弹出“确认退出战斗吗?”，弹框期间战斗继续，强力阵容约5秒即胜，因此退出链路去掉了默认延迟。`BattleExit_1~5` 覆盖其它按钮样式，一并保留；新增皮肤样式按 `BattleExit_<n>.png` 顺延编号。
