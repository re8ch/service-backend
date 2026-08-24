# MikroTik 配置基线

## 地址与端口

- 网关：`192.168.88.1/24`
- ether6：接 A8-C LAN1；MikroTik 地址 `192.168.1.2/24`
- ether10：接 GXW4108；链路观察为 100 Mbps full-duplex
- GXW4108 DHCP reservation：`192.168.88.249`
- R640：`192.168.88.251`
- Mac mini：`192.168.88.254`

## A8-C 管理入口

Mac mini 通过 MikroTik 现有端口映射访问：

```text
http://192.168.88.1:18081/  ->  http://192.168.1.1/
```

这条路径只用于管理页面，不承载 A8-C 的电信 Voice_R_43。

## SIP 数据路径

R640 和 GXW4108 同在 `192.168.88.0/24`，正常情况下二者流量在桥内直接
交换，不需要 NAT 或策略路由。A8-C 与 GXW4108 之间的通话是模拟双线，
不是从 A8-C LAN1 转发 SIP。

## 交接检查命令

执行前通过安全渠道取得管理员凭据，不要把密码写入脚本或 shell 历史：

```routeros
/interface ethernet monitor ether6 once
/interface ethernet monitor ether10 once
/ip address print where interface=ether6
/ip dhcp-server lease print where address=192.168.88.249
/ip firewall service-port print where name=sip
/ip firewall nat print detail
/ip firewall filter print detail
```

若 SIP helper 为 enabled，先导出配置、确认无其他现网 SIP 依赖，再安排
维护窗口禁用：

```routeros
/ip firewall service-port set sip disabled=yes
```
