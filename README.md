<!-- markdownlint-disable MD033 MD041 -->
<p align="center">
  <img alt="LOGO" src="./favicon.png" width="256" height="256" />
</p>

<div align="center">

# MaaOnmyoji

阴阳师小助手，绝赞开发中~~

**建议使用 [MaaYYs](https://github.com/TanyaShue/MaaYYs) 或 [OnmyojiAutoScript](https://github.com/runhey/OnmyojiAutoScript), 功能更全、更稳定！**

</div>

## TODO

Status: `[ ]` not started, `[-]` in progress, `[!]` blocked, `[x]` done.

### P0 基础功能

- [x] 启动游戏
- [x] 关闭游戏
- [x] 通用重复刷本
- [x] 单人御魂副本
- [x] 页面识别与跨任务导航
- [-] 单人探索
- [-] 御魂副本-队长
- [-] 御魂副本-队员
- [x] 单人觉醒副本
- [-] 觉醒副本-队长
- [-] 觉醒副本-队员
- [-] 结界突破
- [-] 结界突破-打m退n
- [] 地域鬼王
- [] 契灵-探索
- [] 契灵-挑战
- [x] 自动领取奖励

### P1 进阶功能

- [] 奖励统计与上报

## 模拟器设置

1. **分辨率 16:9，推荐 1280×720。** 识别区域和图片素材按 1280×720 截图标定；每个任务开始时都会检查画面比例，不是 16:9 会直接停止。1920×1080 等其它 16:9 分辨率会缩放到短边 720 后识别，但没有逐项验证。
2. **常态横屏。** 建议把模拟器设为平板模式或固定横屏。启动前是竖屏、启动游戏后才转横屏，可能导致截图方向错误。
3. **开启 ADB 调试**，控制器选择“安卓端”。启动和关闭游戏通过 ADB 启停应用，启动完成判断还会通过 ADB 读取设备时间，所以“启动游戏”任务不支持桌面端。
4. **系统时间使用 24 小时制并保持准确。** 启动任务会把庭院左上角状态栏的时间与设备时间对照（允许前后 1 分钟），12 小时制在下午对不上。
5. **客户端包名。** “启动游戏”“关闭游戏”针对官服模拟器渠道包 `com.netease.onmyoji.wyzymnqsd_cps`。其它渠道包需要修改 `Startup.json`、`Shutdown.json` 中的 `package`，或手动启动游戏后再运行其它任务。
6. **关闭常驻小纸人。** 点击界面上的智能小纸人进入设置，选择不再常驻，避免它遮挡按钮。
7. **庭院皮肤与界面。** 素材按项目截图中的界面采集，更换庭院皮肤或界面样式可能导致启动完成判断和页面识别失败。

## 使用说明

自定义的功能参考 **[Example.json](./assets/resource/base/pipeline/Example.json)**, 其余请参考 **[MaaFramework](https://github.com/MaaXYZ/MaaFramework)** 的文档

## 免责声明

本项目仅供学习和研究使用，不得用于任何商业用途。使用本项目所产生的一切后果由用户自行承担。

## 鸣谢

本项目由 **[MaaFramework](https://github.com/MaaXYZ/MaaFramework)** 强力驱动！

感谢以下开发者对本项目作出的贡献:

[![Contributors](https://contrib.rocks/image?repo=sxf51/MaaOnmyoji&max=1000)](https://github.com/sxf51/MaaOnmyoji/graphs/contributors)
