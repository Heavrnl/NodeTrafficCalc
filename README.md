# 📊 NodeTrafficCalc - 多实例月流量计算器

## 📝 项目简介

**NodeTrafficCalc** 是一个专为解决 Grafana 中不同实例有不同月流量重置日的问题而设计的工具。在传统的监控系统中，所有实例通常共享同一个流量统计周期，这无法满足不同 VPS 服务商有不同计费周期的实际需求。本项目通过自定义计算和指标推送，使每个实例能够根据其特定的月重置日来计算和展示流量数据。



## ✨ 主要功能

- **📆 自定义重置日期**：为每个实例单独配置月流量重置日
- **📊 多指标支持**：提供上传、下载和总流量三种指标
- **🔧 灵活配置**：通过 YAML 配置文件进行设置，支持热重载
- **💻 仅需在主控端部署**：无需在每个被监控节点上安装额外组件

## 🚀 快速启动

### 💼 1. 克隆仓库

```bash
git clone https://github.com/Heavrnl/NodeTrafficCalc
cd NodeTrafficCalc
```
### ⚙️ 2. 配置实例

编辑配置文件，在 instances 部分添加你的实例和对应的重置日：
```bash
vi NodeTrafficCalc/config.yaml
```

### 📝 配置示例

```yaml
instances:
  # 香港 VPS，每月 15 号重置
  - identifier: "HK-VPS"  # 必须与 Prometheus 中的 'instance' 标签完全匹配
    reset_day: 15         # 重置日期 (1-31)

  # 美国 VPS，每月 1 号重置
  - identifier: "US-VPS"
    reset_day: 1
```

### ▶️ 3. 启动服务
```bash
docker-compose up -d
```

## 🔧 工作原理

1. 从配置文件中读取每个实例的标识符和重置日期
2. 根据当前日期和实例的重置日，计算当前计费周期的开始时间
3. 从 Prometheus 查询该实例在计费周期内的网络流量数据
4. 计算周期内的流量增长值
5. 将计算结果推送到 Pushgateway，包含实例标识符和重置日标签
6. Prometheus 从 Pushgateway 抓取数据，Grafana 展示结果

## 📈 在 Grafana 中使用

### ⬆️ 上传流量：
```
instance_monthly_transmit_bytes_increase{job="monthly_traffic_calculator", instance=~"$node"}
```

### ⬇️ 下载流量：
```
instance_monthly_receive_bytes_increase{job="monthly_traffic_calculator", instance=~"$node"}
```

### 📊 总流量：
```
instance_monthly_total_bytes_increase{job="monthly_traffic_calculator", instance=~"$node"}
```


