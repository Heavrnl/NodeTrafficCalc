上传
```
instance_monthly_transmit_bytes_increase{job="monthly_traffic_calculator", device=~"eth.*|ens.*|eno.*|enp.*|enx.*|enX.*|wlan.*|venet.*", instance=~"$node"}
```

接收
```
instance_monthly_receive_bytes_increase{job="monthly_traffic_calculator", device=~"eth.*|ens.*|eno.*|enp.*|enx.*|enX.*|wlan.*|venet.*", instance=~"$node"}
```

总和
```
instance_monthly_total_bytes_increase{job="monthly_traffic_calculator", device=~"eth.*|ens.*|eno.*|enp.*|enx.*|enX.*|wlan.*|venet.*", instance=~"$node"}
```
