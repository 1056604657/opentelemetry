# Kubernetes OpenTelemetry 生产采集平台

面向 200 微服务的 Kubernetes 三信号（traces / metrics / logs）采集与处理平台：OpenTelemetry Operator + 自研 `otel-platform` chart（只描述 Collector CR 与支撑资源，由 Operator 生成工作负载）。**本包只安装采集和处理层**，三个后端的写入口、认证和证书需先准备；普通 Elasticsearch 的 9200 接口不能直接填为 OTLP 日志地址。

完整安装步骤见[部署文档](02-部署文档.md)；组件原理、数据处理、容量及运维细节见[架构文档](01-架构文档.md)。

```text
production-observability/
├── 01-架构文档.md
├── 02-部署文档.md
├── values-platform.yaml       # 复制一份，填写每个集群的信息
├── values-telemetry.yaml      # 通用三信号处理及尾采样
├── values-operator.yaml       # Operator配置
├── charts/
│   ├── opentelemetry-operator-0.126.1.tgz
│   ├── otel-platform-0.7.8.tgz
│   └── otel-platform/         # 当前安装包对应源码，日后维护使用
└── new-dev集群验证/            # 仅保留已有本地集群配置与凭据，不入库
```

## 快速开始

```bash
# 1. 复制集群模板：填写身份、后端和凭据；默认不使用 PVC，开启时再填写 StorageClass
cp values-platform.yaml values-<集群名>.local.yaml

# 2. 安装 Operator（确认集群内没有已存在的 Operator 或 CRD）
helm upgrade --install otel-operator ./charts/opentelemetry-operator-0.126.1.tgz \
  --kube-context <context> --namespace opentelemetry-operator-system --create-namespace \
  -f values-operator.yaml --wait --timeout 10m

# 3. 安装采集平台（telemetry values 在前、集群 values 在后，后者覆盖前者）
helm upgrade --install otel-platform ./charts/otel-platform-0.7.8.tgz \
  --kube-context <context> --namespace observability --create-namespace \
  -f values-telemetry.yaml -f values-<集群名>.local.yaml --wait --timeout 10m
```

字段含义、后端地址格式、 rollout 验收命令见[部署文档](02-部署文档.md)；接入第一个 Java 服务、灰度与推广流程见同文档第 5 节起。

## 三个 YAML 与 PVC 开关

- `values-platform.yaml`：复制成每集群配置，先选存储模式，再填地址、凭据、节点证书和资源。
- `values-telemetry.yaml`：三信号处理规则和尾采样；不需要为存储模式复制一份。
- `values-operator.yaml`：Operator、Webhook和注入设置；不需要配置PVC。

```yaml
# 默认：所有 Collector 不创建 PVC。
persistent_queue: false
storage_class: ''
pvc_size: 10Gi  # false 时不生效
```

需要持久发送队列时，在同一份集群配置中改为`persistent_queue: true`并填写云块盘`storage_class`。按默认副本数，unified创建7个PVC，dual/direct为9个、dual/kafka为7个。

无PVC时OTLP/Kafka队列使用内存，异常退出可能丢失未发送数据；Node仍用hostPath保存日志读取位置，Cluster事件checkpoint及Remote Write WAL使用临时卷。已有平台切换前必须排空队列并处理StatefulSet不可变卷模板；旧PVC不自动删除，详见[存储开关与切换](01-架构文档.md#内存队列)。

## 日志轮转修正（0.7.8）

Node自动发现活动及未压缩轮转日志，排除gzip/tmp；默认与受限namespace两条生成路径同步修正，无需手动追加接收器字段。存量升级可能补读历史并产生重复，先看[升级步骤](02-部署文档.md#日志轮转升级)，保留原节点checkpoint目录。0.7.7包保留供版本/回滚对照；回滚不撤回后端已写入的数据。

## new-dev 历史验证结论（2026-10-24 压测）

以下为 0.7.5、30 秒尾采样窗的历史压测摘要，不代表 0.7.6 的峰值容量，也未覆盖超过 64 文件时的日志饥饿。0.7.6 修正文件遍历、默认 60 秒尾采样窗及空 Metric 清理；整改结果见 [F1–F3 整改验证报告](F1-F3整改验证-2026-10-26.md)，完整风险清单见 [生产部署前全面分析](生产部署前全面分析报告-2026-10-26.md)。

| 链路 | 结论 |
|---|---|
| traces | 6,405 traces/s 满载下保留率与采样策略精确一致（旧配置 667 traces/s 即断崖，修复 9.6 倍）；gateway 双副本 32,024 spans/s 零拒绝 |
| logs | 导出吞吐 ~95 → 3,877 条/s（41 倍）、全链零丢失；**后端持续摄入上限 ~3.8k 条/s**，200 微服务上线前需按实际日志量复核后端容量 |
| 注入 | Java 17/21 + Spring Boot 3.5.16 真实自动注入，Prometheus / ES / Tempo 三信号低流量验证通过 |

详细报告已按精简要求清理，历史验证摘要与边界见[架构文档](01-架构文档.md#版本验证)；新集群按部署文档重新验收。

## 新集群安装前必读

1. **`values-platform.yaml` 是模板不是默认值**：`cluster_name` 等必填字段未替换即被 `validate.yaml` 拒绝安装；`storage_class` 仅在开启PVC时必填，必须复制为 `values-<集群名>.local.yaml` 填写；该文件含真实凭据，**不要入库或分享**。
2. **`num_traces` 按集群速率与副本数核算**：公式 `num_traces ≥ 1.5 × 集群 traces/s × decision_wait / sampler_replicas`，当前 60 秒窗、两副本按 4,000 traces/s 和 1.5 倍余量需 180000 条/副本，200000 提供数量余量；实际 RSS/吞吐仍需验收（chart 默认值与随包 `values-telemetry.yaml` 一致，均为 200000 / 300000×2）；超限是静默断崖（无报错、保留归零），务必按告警 `sampled/new_trace_id_received` 监控。
3. **`log_namespaces` / `otlp_namespace_selector` / `monitor_namespace_selector`**：new-dev 的收紧值只适用于测试集群，生产留默认（全集群 + opt-in 注解过滤），照抄会只采 2 个 namespace。
4. **`collector_memory_mib`**：与节点/集群实际可调度资源核对后再调整，memory_limiter 与 GOMEMLIMIT 会自动联动。
5. **new-dev 专属参数不复制到生产**：CA、节点 hostAliases 映射、采集 namespace 限制、访问凭据仅适用于该测试集群（见[云环境适配](01-架构文档.md#云环境)）。
6. **选择器要看最终合并结果**：全新生产配置默认`{}`正常；只有叠加受限values后，再写`{}`才无法清除旧matchLabels/matchExpressions。生产不加载new-dev覆盖文件，恢复范围按[选择器合并](01-架构文档.md#选择器合并)检查最终CR和跨namespace抓取，不要求所有场景统一写null。
7. **告警先核对真实标签**：本次`up{job=~"otel-.*"}`只匹配Operator、漏掉Collector；不是永远返回空。Collector按实际集群/namespace/Pod标签匹配，并同时检测少副本与整组缺数，见[自监控告警](01-架构文档.md#自监控告警)。

## 维护说明

- chart 源码在 `charts/otel-platform/`，与 `otel-platform-0.7.8.tgz` 对应；**修改源码后必须重新打包**（`helm package charts/otel-platform -d charts`），不能只改目录却继续装旧包。
- Operator chart（`opentelemetry-operator-0.126.1.tgz`）保持官方原样，未做任何修改；平台差异全部通过 `values-*.yaml` 覆盖。
- 升级用 `helm upgrade`（命令同快速开始的平台安装步骤）；collector `terminationGracePeriodSeconds=120`，滚动更新旧实例排水最长约 2 分钟，`rollout status` 超时建议设 ≥240s。
- 测试脚本、原始数据、执行计划和测试备份已清理；验证结论保留在分析及整改报告中。安装只依赖 Helm、kubectl 和部署文件。0.7.7归档保留供回滚核对。0.7.7默认不使用PVC；已有PVC部署升级时须显式保留true，切换另按部署文档执行。
