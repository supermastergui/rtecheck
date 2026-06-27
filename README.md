# rtecheck

**公版航线数据 → MTEP Route Check 格式转换工具。**

读取中国民航公版航线数据（FLIGHT_AIRLINE / FLIGHT_AIRLINE_POINT），
输出 MTEP Route Check 格式的 `out/RC.csv` 和全点直飞格式的 `out/FULL.csv`，
支持 NAIP 航路检测、Modified 航路生成、方向判定与限制备注合并。

## 数据流

```
res/FLIGHT_AIRLINE.csv ──────┐
res/FLIGHT_AIRLINE_POINT.csv ─┤
res/ISEC.txt ────────────────┤
res/ROUTE_RESTRICT.csv ─────┤
res/ROUTE_RESTRICT_RTE.csv ─┘
        │
        ▼
    main.py
        │
        ├── out/RC.csv   （MTEP Route Check 格式）
        └── out/FULL.csv （全点直飞展开）
```

## 输入文件

| 文件 | 编码 | 用途 |
|------|------|------|
| `FLIGHT_AIRLINE.csv` | GB18030 | 航线元数据（起降机场、过渡高度、最低安全高度） |
| `FLIGHT_AIRLINE_POINT.csv` | GB18030 | 航线航段序列（点-航路-点，支持 VOR/NDB 名称解析） |
| `ISEC.txt` | UTF-8 | 约 27 万个导航点坐标（用于飞行方向 SE/SO 判定） |
| `ROUTE_RESTRICT.csv` | GB18030 | 限制备注主表 |
| `ROUTE_RESTRICT_RTE.csv` | GB18030 | 限制与航段 UUID 关联表 |

## 输出文件

### out/RC.csv — MTEP Route Check

8 列标准格式：

| 列 | 含义 |
|----|------|
| Dep | 起飞机场 ICAO |
| Arr | 到达机场 ICAO |
| Name | 航线名称（NAIP 航线有 `-Modified` 后缀） |
| EvenOdd | 方向：`SE`（西行偶数高度层）/ `SO`（东行奇数高度层） |
| AltList | 过渡高度层列表，格式 `S84` 或 `S84 / S78 / S72` |
| MinAlt | 最低安全高度 |
| Route | 航路字符串 |
| Remarks | 备注（限制信息 / NAIP 标记） |

**航线行类型：**

- **非 NAIP 航线** — 输出原始行（1 行）
- **NAIP 航线** — 输出原始行 + Modified 行（2 行）

| 行类型 | Name 后缀 | Route 内容 |
|--------|-----------|------------|
| 原始行 | 无 | 完整压缩后的航路，包含 NAIP 航路 |
| Modified 行 | `-Modified` | 移除 NAIP 航路（H/Z/J/X/V 及 FANS）和 P 点，将非 P 中间点展开为直飞连接 |

### out/FULL.csv — 全点展开

每条航线输出一行，航路点全部展开（**不做航路压缩**），不包含任何航路名，P 点保留。适用于需要独立校验每个中间点的场景。

## 安装

```bash
git clone https://github.com/<your-org>/rtecheck.git
cd rtecheck
# Python 3.8+，仅需标准库
```

## 使用

```bash
python main.py
```

输出文件在 `out/` 目录下。

## 算法

### 航路压缩 (`compress_route`)

将连续相同航路折叠，保留起止点：

```
BOPTU W162 KAKMI W162 WLY   →   BOPTU W162 WLY
```

### 方向判定 (`compute_evenodd`)

通过 ISEC 坐标比较航线首尾点经度：
- 终点经度 < 起点（西行）→ `SE`（偶数高度层）
- 终点经度 > 起点（东行）→ `SO`（奇数高度层）

### NAIP 检测

NAIP 航路匹配正则 `^[HZJXV]\d+$`（H57、Z1、J51 等）或关键字 `FANS`。
P 点（`^P\d+$`）也触发 NAIP 标记。

### Modified 行生成

对于每段 NAIP 航路（如 `JDZ H2 QO`）：
1. 从原始（未压缩）航段数据中提取中间的所有航路点
2. 过滤掉 P 点（`^P\d+$`）
3. 保留的非 P 点作为直飞连接点插入路线中

例：

```
压缩版：   XSH H17 JDZ H2 QO
原始展开： XSH P36 DYTES KAKBA P575 JDZ P395 P263 … OMDEM … P610 QO
Modified： XSH DYTES KAKBA JDZ OMDEM QO
                     ↑ H17 段                    ↑ H2 段
```

### Full 直飞生成

从原始航段直接提取所有航路点（偶数索引位），不做航路压缩，也不区分 NAIP。

## 项目结构

```
rtecheck/
├── main.py            # 主程序
├── README.md
├── LICENSE
├── res/               # 输入数据（需自行获取公版数据）
│   ├── FLIGHT_AIRLINE.csv
│   ├── FLIGHT_AIRLINE_POINT.csv
│   ├── ISEC.txt
│   ├── ROUTE_RESTRICT.csv
│   └── ROUTE_RESTRICT_RTE.csv
└── out/               # 输出目录
    ├── RC.csv
    └── FULL.csv
```

## 依赖

- Python 3.8+
- 标准库：`csv`, `re`, `os`, `collections`

## 许可

MIT License © 2026

## 术语

| 缩写 | 全称 |
|------|------|
| NAIP | National Aeronautical Information Publication |
| MTEP | Miscellaneous Tag Enhancement Plugin ([MTEPlugin-for-EuroScope](https://github.com/KingfuChan/MTEPlugin-for-EuroScope)) |
| SE | 西行航线（South-East，偶数高度层） |
| SO | 南行航线（South-Out，奇数高度层） |
| FANS | Future Air Navigation System |
