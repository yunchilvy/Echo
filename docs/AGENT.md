# Echo 无线设备电量台 · 给 AI 的说明书

> 把本文件整段复制给任意 AI（Claude / GPT / DeepSeek / Copilot 都行），它就能看懂
> 这个软件怎么工作，并帮你把一台"读不到电量"的 2.4G 设备接进来。
> 文末有"现场数据"该长什么样，App 里点「复制排查资料给 AI」会自动拼好。

---

## 1. 这是什么

Windows 桌面小工具（Python + pywebview + WebView2，纯 ctypes 访问系统 API，无第三方
依赖）。它把三类无线设备的电量收在一张清单里：

| 类别 | 电量从哪来 | 代码位置 |
| --- | --- | --- |
| 蓝牙耳机 / 音箱 | Windows 蓝牙栈把 HFP 的 `AT+XAPL` 上报写进设备节点的未公开属性 `{104EA319-6EE2-4701-BD47-8DDBF425BBE5}` 第 2 项 | `core/btbatt.py` |
| 蓝牙"是否真连着" | `MMDevices` 里同名音频端点的 `DeviceState`（只有 ACTIVE 才算连着） | `core/audioend.py` |
| 2.4G 键鼠 / 耳机 | ① 标准 HID 电量字段 ② 厂商查询协议 ③ 主动上报帧 | `core/protocols.py` + `core/pushmon.py` |

**不知道电量时宁可显示"—"，也不编数字。** 这是这个项目的底线。

## 2. 目录结构

```
app.py              # 入口：单实例、建窗、托盘、JS 桥（Api 类）
ui/index.html       # 全部界面：单文件，内联 CSS/JS，无构建步骤
core/hidwin.py      # HID 访问层（setupapi/hid/kernel32，纯 ctypes）
core/protocols.py   # 电量读取策略引擎 + 内置厂商协议表
core/pushmon.py     # 主动上报帧的常驻监听线程
core/devices.py     # 把蓝牙与 HID 两路合成一份设备清单
core/sniff.py       # 通用嗅探：不认识协议时抓原始帧
core/btbatt.py      # 蓝牙电量（读设备属性）
core/audioend.py    # 音频端点状态（判断蓝牙是否真连接）
core/store.py       # 配置：%APPDATA%\Echo\config.json
core/tray.py        # 托盘图标（Shell_NotifyIconW）
core/winicon.py     # 纯 Python 画图标（SDF 描边光栅器，无 Pillow）
docs/AGENT.md       # 本文件
```

## 3. 关键概念（改造前必须懂）

### 3.1 HID 路径与分组

Windows 里每个 HID **集合**（collection）是一条独立路径，长得像：

```
\\?\hid#vid_1d57&pid_fa65&mi_02&col01#8&2f1c3a4&0&0000#{4d1e55b2-...}
```

- `vid_1d57` / `pid_fa65` = 厂商 / 产品 ID
- `mi_02` = 接口号，`col01` = 集合号 → 同一个接收器会有好几条
- `hidwin.physical_group_key()` 去掉 `mi_` 与 `col` 段，把同一物理设备归为一组

同一个接收器不同集合可能分工完全不同：键盘走 `0x0001:0x0006`，厂商通道走 `0xFF00`，
电量说不定落在某个不起眼的 `0x000A:0x0000` 小接口上（英菲克就是）。

### 3.2 打开的三种姿势

| 需要的权限 | 用途 | 说明 |
| --- | --- | --- |
| `dwDesiredAccess = 0` | 只查属性、能力表、Feature Report | 鼠标键盘的顶层集合被系统独占，只能用这个姿势 |
| `GENERIC_READ + FILE_FLAG_OVERLAPPED` | 收 Input Report | 必须用独立读句柄 + 事件超时 + `CancelIoEx` |
| `GENERIC_READ\|WRITE` | 发查询帧、读回复 | 同上，**绝不在同一句柄上并发"阻塞读 + 写"** |

### 3.3 三条读取路线（按可靠性排序）

**A. 标准 HID 电量字段** —— 描述符里声明了就没歧义，优先级最高：

```
Usage Page 0x0006 Usage 0x0020   Battery Strength
Usage Page 0x0085 Usage 0x0001   Absolute State of Charge
Usage Page 0x0085 Usage 0x0066   Relative State of Charge
```

**B. 厂商查询协议** —— 发一帧、等回复、按偏移取字节。内置表里就是这一层：
罗技 HID++ 2.0（Feature 0x1004/0x1000）、ATK/VXE/VGN、VAXEE/ZYGEN、Endgame、
Nordic nRF、杰理 TLV。

**C. 主动上报** —— 设备每 ~2 秒自己推一包，只监听不问。实测帧：

```
03 <型号码> 40|41 <充电状态> <电量>
   ↑ 03 = 固定头
   ↑ 型号码 ∈ {0x73,0x90,0x93,0x95,0x98,0x99}（实测集合，可能不全）
   ↑ 40 = 未充电 / 41 = 充电中
   ↑ 充电状态 1=未充电 2=已充满 3=充电中
   ↑ 电量 0..100 十进制
例：03 99 40 01 56  →  86%
```

判定规则：**8 秒没有新帧就算离线**（`pushmon.STALE_AFTER`），但保留最后读数显示为
"上次 xx%"，因为那个值通常仍然准确。

## 4. 给新设备补一套协议：三条路

### 路径 1（首选）：写 `%APPDATA%\Echo\protocols.json`

不用改代码，重启即生效。字段含义：

```json
[
  {
    "name": "我的接收器",
    "vid": "1D57",            // VID，十六进制字符串或整数
    "usage_page": "FF00",      // 接口的 Usage Page（见嗅探输出）
    "report_id": "08",         // 报文首字节
    "payload": [125, 114, 2, 0, 1, 7, 1],   // 报告号之后的负载
    "length": 64,              // 发送长度
    "level_offset": 7,         // 回复里电量的字节下标（从 0 开始）
    "wait_ms": 100,            // 发完等多久再读
    "scale": 1,                // 电量 = 该字节 × scale
    "level_max": 100,          // 该字节的合法上限，超出即判为失败
    "mode": "output",          // output | feature | auto
    "expect": [[1, 114], [5, 7]]  // 可选：回复必须满足 offset=value，用来校验
  }
]
```

`mode` 怎么选：接口有 `FeatureReportByteLength` 且设备不吃 Output Report → `feature`；
否则 `output`。不确定就写 `auto`。

### 路径 2：改代码，加进 `core/protocols.py` 的 `BUILTIN`

当设备需要多帧握手、CRC、或回复是 TLV 结构时（参考 `strategy_jieli()` 和
`strategy_logitech()`）。新写一个 `strategy_xxx(iface) -> Reading`，然后在
`read_battery()` 的 `steps` 里排上。注意：单策略抛异常不能影响其它策略。

### 路径 3：只做监听（设备会自己推包时）

如果 sniff 显示某个接口每两秒稳定吐数据，而且里面有字节在 0..100 之间变化，
那它八成就是主动上报。把帧格式写进 `core/pushmon.py` 的 `parse_frame()`：

```python
if len(frame) >= 5 and frame[0] == 0x03 and frame[1] in TYPECODES and frame[2] in (0x40, 0x41):
    level, charge = frame[4], frame[3]
```

`TYPECODES` 是白名单，把嗅探到的型号码加进去即可。

## 5. 怎么确认"哪个字节是电量"

这是整个适配过程唯一需要动脑的地方。可靠做法：

1. **先看长度和稳定性**：同一接口的帧长度固定、前 1–3 字节固定，通常就是报文头。
2. **找 0..100 的字节**：逐个字节看取值范围，超出 100 的直接排除。
3. **做减法**：给设备充电 / 用一会儿，再抓一次，对比哪一字节变了。**只有电量该变。**
   （充电状态字节也会变，通常是 1/2/3）
4. **交叉验证**：读数是 86，系统或厂商软件也显示 86 → 就是它。
5. **别信巧合**：帧里的 `86` 可能是计数器。至少观察三次不同电量。

App 里的「嗅探 8 秒」会直接标出候选字节；「复制嗅觉结果给 AI」会把原始帧打包发出去。

## 6. 已知边界（不要承诺做不到的事）

- 有些百元级键鼠套装的接收器**既不查询也不上报**，电量只存在于厂商驱动里。
  这种设备只能显示"—"。
- 设备休眠后不响应查询，需要先动一下设备（唤醒）再读。
- 同一 HID 集合同时开两个读句柄会**抢报告**：Echo 的常驻监听和嗅探会互相影响，
  诊断时会临时丢帧，属正常。
- 蓝牙电量依赖设备固件是否上报；不上报的耳机在系统设置里也看不到百分比。
- 电量低于 10% 会弹一次预警（每台每次跌破只弹一次，回到 10% 以上重新武装）。

## 7. 现场数据附录（App 会自动附在你的问题后面）

```json
{
  "device": {
    "key": "hid:\\?\hid#vid_1d57&pid_fa65...",
    "name": "Inphic KP 8K", "kind": "combo", "link": "2.4g",
    "vid_pid": "1D57:FA65", "level": 22, "charged": false
  },
  "probe": {
    "vid_pid": "1D57:FA65", "usage": "0x000A:0x0000",
    "in_len": 5, "out_len": 0, "feature_len": 0,
    "attempts": [{"label": "0x08/0x72 电量查询", "sent": "087d72020001...", "replies": []}]
  },
  "sniff": {
    "seconds": 8,
    "interfaces": [{
      "vid_pid": "1D57:FA65", "usage": "0x000A:0x0000", "in_len": 5,
      "frames": [{"hex": "03 99 40 01 56", "count": 4}],
      "battery": [{"byte": 4, "values": [86], "guess": "86%"}],
      "push": {"level": 86, "charge": 1, "hex": "03 99 40 01 56"}
    }]
  }
}
```

## 8. 请 AI 做的事（可直接照抄）

> 上面是 Echo 的原理和现场数据。
> 1. 判断这台接收器属于"标准字段 / 查询协议 / 主动上报"哪一类，说明依据；
> 2. 如果数据够，直接给出可写进 `protocols.json` 的完整条目，或给出需要改
>    `pushmon.parse_frame()` / `protocols.BUILTIN` 的最小 diff；
> 3. 如果数据不够，告诉我下一步该抓什么（要插拔、要充电、要按哪个键），
>    以及判断标准是什么；
> 4. 不确定就说不确定，不要猜一个能跑但会显示错数字的方案。
