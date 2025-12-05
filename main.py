
import requests
import yaml
import time
from datetime import datetime, timedelta, timezone
import logging
from logging import handlers
import sys
from urllib.parse import quote_plus
import signal
import os
import re


CONFIG_FILE = 'config.yaml'
# 用于控制主循环的标志
running = True
# 日志记录器实例 (稍后配置)
logger = logging.getLogger(__name__)

# --- 默认配置 (如果配置文件中缺少某些键，则使用这些值) ---
DEFAULT_CONFIG = {
    'prometheus_url': 'http://localhost:9090',
    'pushgateway_url': 'http://localhost:9091',
    'push_job_name': 'monthly_traffic_calculator',
    # 日志默认设置
    'logging_enabled': True, # 是否启用日志记录
    'log_file': 'logs/monthly_traffic_calc_service.log', # 默认日志文件路径
    'log_level': 'INFO', # 默认日志级别
    'log_rotation_enabled': True, # 是否启用日志轮转
    'log_max_bytes': 10485760, # 10 MB，日志文件最大大小 (bytes)
    'log_backup_count': 5, # 保留的备份日志文件数量
    # 时间默认设置
    'loop_interval_seconds': 300, # 默认循环间隔 5 分钟
    # 指标默认设置
    'metric_names': {
        'tx_increase': "instance_monthly_transmit_bytes_increase",
        'rx_increase': "instance_monthly_receive_bytes_increase",
        'total_increase': "instance_monthly_total_bytes_increase",
        'monthly_limit': "instance_monthly_limit_bytes",
        'remaining_bytes': "instance_monthly_remaining_bytes",
        'instance_info': "instance_info",  # 实例信息指标，用于展示所有标签
    },
    'base_metrics': {
        'tx': 'node_network_transmit_bytes_total',
        'rx': 'node_network_receive_bytes_total',
    },
    # 设备过滤器 - 默认使用常见网络接口名称
    'device_filter_increase': 'device=~"eth.*|ens.*|eno.*|enp.*|enx.*|enX.*|wlan.*|venet.*"',
}


def setup_logging(config):
    """根据配置设置日志记录器 (使用相对日志路径)"""
    # 从配置字典获取日志设置，如果键不存在则使用默认值
    enabled = config.get('logging_enabled', DEFAULT_CONFIG['logging_enabled'])
    # log_file 现在是相对路径, 如 'logs/monthly_traffic_calc_service.log'
    log_file = config.get('log_file', DEFAULT_CONFIG['log_file'])
    log_level_str = config.get('log_level', DEFAULT_CONFIG['log_level'])
    rotation_enabled = config.get('log_rotation_enabled', DEFAULT_CONFIG['log_rotation_enabled'])
    try:
        max_bytes = int(config.get('log_max_bytes', DEFAULT_CONFIG['log_max_bytes']))
    except (ValueError, TypeError):
        max_bytes = DEFAULT_CONFIG['log_max_bytes']
        print(f"警告：无效的 log_max_bytes 配置值，使用默认值: {max_bytes}", file=sys.stderr)
    try:
        backup_count = int(config.get('log_backup_count', DEFAULT_CONFIG['log_backup_count']))
    except (ValueError, TypeError):
        backup_count = DEFAULT_CONFIG['log_backup_count']
        print(f"警告：无效的 log_backup_count 配置值，使用默认值: {backup_count}", file=sys.stderr)

 
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()


    if not enabled:
        logger.setLevel(logging.CRITICAL + 1)
        logger.addHandler(logging.NullHandler())
        print("日志记录已根据配置禁用。", file=sys.stderr)
        return

    # 设置日志级别
    log_level_map = {
        'DEBUG': logging.DEBUG, 'INFO': logging.INFO, 'WARNING': logging.WARNING,
        'ERROR': logging.ERROR, 'CRITICAL': logging.CRITICAL,
    }
    log_level = log_level_map.get(str(log_level_str).upper(), logging.INFO)
    logger.setLevel(log_level)

    # 定义日志格式
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # 文件处理器
    try:
        # 检查并创建日志文件所在的目录 (相对路径)
        # 对于 'logs/file.log', os.path.dirname 会返回 'logs'
        # 对于 'file.log', os.path.dirname 会返回 '' (空字符串)
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
             try:
                 # exist_ok=True 使得如果目录已存在也不会报错
                 os.makedirs(log_dir, exist_ok=True)
                 print(f"创建日志目录: {log_dir}", file=sys.stderr) # 初始创建时打印
             except OSError as e:
                 # 如果创建目录失败，记录错误到 stderr
                 print(f"警告：无法创建日志目录 '{log_dir}': {e}", file=sys.stderr)
                 # 这里不抛出异常，尝试继续，但文件处理器可能设置失败

        # 根据是否启用 rotation 选择处理器
        if rotation_enabled:
            file_handler = handlers.RotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8'
            )
            if logger.isEnabledFor(logging.INFO):
                 logger.info(f"启用日志轮转: maxBytes={max_bytes}, backupCount={backup_count}")
        else:
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            if logger.isEnabledFor(logging.INFO):
                 logger.info("禁用日志轮转，使用普通文件日志。")

        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        if logger.isEnabledFor(logging.INFO):
             # 使用 os.path.abspath 获取文件的绝对路径以供日志记录，即使配置是相对的
             abs_log_path = os.path.abspath(log_file)
             logger.info(f"日志文件路径 (实际): {abs_log_path}")

    except Exception as e:
        # 如果设置文件处理器失败，打印错误到 stderr 并尝试记录警告
        print(f"警告：无法设置日志文件处理器 '{log_file}': {e}", file=sys.stderr)
        logger.warning(f"无法设置日志文件处理器 '{log_file}': {e}")

    # 控制台处理器 (始终添加，除非 logging_enabled=False)
    stream_handler = logging.StreamHandler(sys.stdout) # 或 sys.stderr
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # 配置完成后记录最终的日志级别
    if logger.isEnabledFor(logging.INFO):
         logger.info(f"日志级别设置为: {logging.getLevelName(logger.level)}")


def load_config():
    """读取并合并配置文件 (相对于脚本目录) 和默认配置"""
    config = {}
    # 深拷贝默认配置，防止修改影响后续循环
    for key, value in DEFAULT_CONFIG.items():
        if isinstance(value, dict):
            config[key] = value.copy()
        else:
            config[key] = value

    try:
        # 输出关键调试信息到 stderr (在 logger 配置完成前可能有用)
        print(f"[{datetime.now()}] DEBUG - 尝试读取配置文件: {CONFIG_FILE} (相对于工作目录)", file=sys.stderr)
        # Python 会在当前工作目录查找相对路径的文件
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            user_config_data = yaml.safe_load(f)
        print(f"[{datetime.now()}] DEBUG - 配置文件 '{CONFIG_FILE}' 读取成功。", file=sys.stderr)

        if user_config_data:
            # 合并通用配置 'config' 部分
            if 'config' in user_config_data and isinstance(user_config_data['config'], dict):
                 user_general_config = user_config_data['config']
                 print(f"[{datetime.now()}] DEBUG - 找到 config 部分: {user_general_config}", file=sys.stderr)
                 # 遍历默认配置的键进行合并
                 for key, default_val in DEFAULT_CONFIG.items():
                     if key in user_general_config:
                         user_val = user_general_config[key]
                         # 如果默认值和用户值都是字典，则合并字典（例如 metric_names）
                         if isinstance(default_val, dict) and isinstance(user_val, dict):
                             print(f"[{datetime.now()}] DEBUG - 合并字典配置项: {key}", file=sys.stderr)
                             # config[key] 已经是 default_val 的拷贝
                             config[key].update(user_val)
                         # 否则，直接使用用户提供的值覆盖默认值 (只有当用户值不是 None 时)
                         elif user_val is not None:
                             print(f"[{datetime.now()}] DEBUG - 使用用户配置项: {key} = {user_val}", file=sys.stderr)
                             config[key] = user_val
                         else:
                             print(f"[{datetime.now()}] DEBUG - 用户配置项 {key} 为 None，保留默认值。", file=sys.stderr)

            # 获取实例列表 'instances' 部分
            if 'instances' in user_config_data and isinstance(user_config_data['instances'], list):
                config['instances'] = user_config_data['instances']
                print(f"[{datetime.now()}] DEBUG - 找到 {len(config['instances'])} 个实例配置。", file=sys.stderr)
            else:
                 config['instances'] = [] # 确保 instances 键存在且为列表
                 print(f"[{datetime.now()}] DEBUG - 配置文件中未找到 'instances' 列表。", file=sys.stderr)
        else:
            # 配置文件为空
            print(f"[{datetime.now()}] WARNING - 配置文件 {CONFIG_FILE} 为空，将使用默认配置。", file=sys.stderr)
            config['instances'] = []

        print(f"[{datetime.now()}] DEBUG - 成功加载并合并配置文件。", file=sys.stderr)

    except FileNotFoundError:
        print(f"[{datetime.now()}] WARNING - 配置文件 {CONFIG_FILE} 未找到 (在当前工作目录)，将使用所有默认配置。", file=sys.stderr)
        config['instances'] = [] # 确保 instances 键存在
    except yaml.YAMLError as e:
        print(f"[{datetime.now()}] ERROR - 配置文件 {CONFIG_FILE} 解析错误: {e}。将使用所有默认配置。", file=sys.stderr)
        config['instances'] = []
    except Exception as e:
         print(f"[{datetime.now()}] ERROR - 读取配置文件时发生未知错误: {e}。将使用所有默认配置。", file=sys.stderr)
         config['instances'] = []

    # 最后再次确保实例列表存在
    if 'instances' not in config:
        config['instances'] = []

    return config


def signal_handler(sig, frame):
    """处理终止信号 (SIGINT, SIGTERM)"""
    global running
    # 尝试使用 logger，如果失败则用 print
    try:
        logger.info(f"接收到信号 {sig}, 准备停止...")
    except:
        print(f"[{datetime.now()}] INFO - 接收到信号 {sig}, 准备停止...", file=sys.stderr)
    running = False

# --- Prometheus 查询和 Pushgateway 推送函数 ---

def query_prometheus(prometheus_url, query, timestamp=None):
    """执行 Prometheus 即时查询 (query API)"""
    params = {'query': query}
    api_endpoint = "/api/v1/query"
    if timestamp: params['time'] = str(timestamp)

    api_url = f"{prometheus_url}{api_endpoint}"
    logger.debug(f"查询 Prometheus API: URL={api_url}, Params={params}")

    try:
        response = requests.get(api_url, params=params, timeout=20)
        response.raise_for_status() # 如果状态码不是 2xx，则引发 HTTPError
        result = response.json()
        if result['status'] == 'success':
            return result['data']
        else:
            logger.error(f"Prometheus API 查询失败. Status: {result.get('status')}, Error: {result.get('error', 'N/A')}. Query: {query}, Params: {params}")
            return None
    except requests.exceptions.Timeout:
        logger.error(f"请求 Prometheus API 超时. URL: {api_url}, Params: {params}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"请求 Prometheus API 失败: {e}. URL: {api_url}, Params: {params}")
        return None
    except Exception as e:
        logger.error(f"处理 Prometheus 响应或请求时发生未知错误: {e}. Response Text: {response.text if 'response' in locals() else 'N/A'}")
        return None


def get_scalar_value(data):
    """从 Prometheus 查询结果中提取标量值"""
    if data and data.get('resultType') == 'vector' and data.get('result'):
        try:
            value_str = data['result'][0]['value'][1]
            return float(value_str)
        except (ValueError, IndexError, KeyError) as e:
            logger.error(f"无法从 vector 结果中提取标量值: {e}. Data: {data}")
            return None
    # 对于 increase 查询，空向量意味着增量为 0
    elif data and data.get('resultType') == 'vector' and not data.get('result'):
        logger.debug(f"查询返回空向量结果，视为 0. Data: {data}")
        return 0.0
    logger.warning(f"查询结果不是有效的标量 vector 或无法处理. Data: {data}")
    return None


def calculate_increase_value(prometheus_url, instance, metric, filters, start_ts, end_ts):
    """使用 increase 函数计算指定时间范围内的增量"""
    duration_seconds = int(end_ts - start_ts)
    if duration_seconds <= 0:
        logger.warning(f"计算 increase 的时间范围无效或为零 for {instance}. Start: {start_ts}, End: {end_ts}. 返回 0。")
        return 0.0

    # 构造 sum(increase(...[range])) 查询
    # 如果 filters 为空，则只使用 instance 过滤器
    if filters and filters.strip():
        increase_query = f'sum(increase({metric}{{instance="{instance}",{filters}}}[{duration_seconds}s]))'
    else:
        increase_query = f'sum(increase({metric}{{instance="{instance}"}}[{duration_seconds}s]))'

    # 查询 end_ts 时间点的 increase 值
    # increase 函数会回顾 [duration_seconds] 的范围
    increase_data = query_prometheus(prometheus_url, increase_query, timestamp=end_ts)
    value = get_scalar_value(increase_data) # get_scalar_value 会处理空向量并返回 0.0

    if value is not None:
        logger.debug(f"计算得到 increase for {instance} from {start_ts} to {end_ts}: {value}")
        return value
    else:
        # 如果 get_scalar_value 返回 None，说明查询失败或结果格式无法处理
        logger.warning(f"计算 increase 失败或结果无效 for {instance}. Query: {increase_query}, Timestamp: {end_ts}")
        return None # 返回 None 表示计算失败


def get_billing_cycle_start(reset_day):
    """计算当前计费周期的开始时间戳 (UTC)"""
    if not isinstance(reset_day, int) or not 1 <= reset_day <= 31:
        logger.error(f"无效的重置日期 (必须是 1-31 的整数): {reset_day}")
        return None
    now = datetime.now(timezone.utc)
    current_year = now.year
    current_month = now.month
    # 确定重置日的年月
    if now.day >= reset_day:
        start_year = current_year
        start_month = current_month
    else:
        # 计算上个月的年月
        first_day_of_current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day_of_last_month = first_day_of_current_month - timedelta(days=1)
        start_year = last_day_of_last_month.year
        start_month = last_day_of_last_month.month
    # 尝试构造日期时间对象
    try:
        start_time = datetime(start_year, start_month, reset_day, 0, 0, 0, tzinfo=timezone.utc)
    except ValueError:
        # 如果 reset_day 对那个月无效（例如 2 月 31 日），则取该月的最后一天
        logger.warning(f"重置日期 {reset_day} 对 {start_year}-{start_month:02d} 无效，使用该月最后一天。")
        # 找到下个月的第一天，然后减去一天
        if start_month == 12:
            next_month_first_day = datetime(start_year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        else:
            next_month_first_day = datetime(start_year, start_month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        last_day_of_month = next_month_first_day - timedelta(days=1)
        # 将时间设为 0 点
        start_time = last_day_of_month.replace(hour=0, minute=0, second=0, microsecond=0)

    return start_time.timestamp() # 返回 Unix timestamp


def push_metric(pushgateway_url, push_job_name, instance, metric_name, help_text, value, reset_day=None, custom_labels=None):
    """推送单个指标到 Pushgateway

    参数:
        pushgateway_url: Pushgateway 的 URL
        push_job_name: 推送使用的 job 名称
        instance: 实例标识符
        metric_name: 指标名称
        help_text: 指标的帮助文本
        value: 指标值
        reset_day: 重置日期 (1-31)，将作为标签添加到指标中
        custom_labels: 自定义标签字典，将被添加到指标中
    """
    if value is None: # 不推送 None 值 (表示计算失败)
        logger.debug(f"值为 None，跳过推送指标 {metric_name} for instance {instance}")
        return False
    # 确保 value 是数字类型
    if not isinstance(value, (int, float)):
        logger.error(f"尝试推送非数值类型的值 for {metric_name}, instance {instance}. Value: {value}, Type: {type(value)}")
        return False

    try:
        instance_encoded = quote_plus(instance)
    except Exception as e:
        logger.error(f"URL 编码 instance '{instance}' 失败: {e}")
        return False

    # 构造符合 Prometheus text format 的 payload
    # 构造标签字符串，包含所有非空参数
    labels_parts = [f'instance="{instance}"']

    if reset_day is not None:
        labels_parts.append(f'reset_day="{reset_day}"')

    # 添加自定义标签
    if custom_labels and isinstance(custom_labels, dict):
        for label_name, label_value in custom_labels.items():
            if label_name and label_value is not None:
                # 转义标签名称和值中的特殊字符
                safe_label_name = str(label_name).replace('"', '\"')
                safe_label_value = str(label_value).replace('"', '\"')
                labels_parts.append(f'{safe_label_name}="{safe_label_value}"')

    # 将所有标签部分用逗号连接
    labels = ', '.join(labels_parts)

    payload = f"""\
# HELP {metric_name} {help_text}
# TYPE {metric_name} gauge
{metric_name}{{{labels}}} {value}
"""
    # 推送 URL 格式: /metrics/job/<JOBNAME>/instance/<INSTANCENAME>
    push_url = f"{pushgateway_url}/metrics/job/{push_job_name}/instance/{instance_encoded}"
    logger.debug(f"推送数据: URL={push_url}, Payload=\n{payload.strip()}")

    try:
        response = requests.post(push_url,
                                 data=payload.encode('utf-8'),
                                 headers={'Content-Type': 'text/plain; version=0.0.4; charset=utf-8'},
                                 timeout=10)
        # Pushgateway 成功推送通常返回 200 或 202
        if response.status_code in [200, 202]:
             logger.info(f"成功推送指标 {metric_name} for instance {instance}: {value}")
             return True
        else:
             # 记录错误，但不认为是致命错误，继续处理其他实例
             logger.error(f"推送指标 {metric_name} 到 Pushgateway 失败 for instance {instance}. Status: {response.status_code}, Response: {response.text[:200]}") # 只记录部分响应
             return False
    except requests.exceptions.Timeout:
        logger.error(f"推送指标 {metric_name} 到 Pushgateway 超时 for instance {instance}. URL: {push_url}")
        return False
    except requests.exceptions.RequestException as e:
        # 捕获连接错误等网络相关异常
        logger.error(f"推送指标 {metric_name} 到 Pushgateway 失败 for instance {instance}: {e}. URL: {push_url}")
        return False
    except Exception as e:
        # 捕获其他可能的错误
        logger.error(f"推送指标 {metric_name} 时发生未知错误 for instance {instance}: {e}")
        return False

# --- 主处理逻辑 ---

def process_instances(config):
    """处理所有实例的流量计算和推送"""
    # 从传入的配置中获取参数
    prometheus_url = config['prometheus_url']
    pushgateway_url = config['pushgateway_url']
    push_job_name = config['push_job_name']
    metric_names = config['metric_names']
    base_metrics = config['base_metrics']
    device_filter_increase = config['device_filter_increase']
    instances = config.get('instances', []) # 从配置获取实例列表，默认为空

    if not instances:
        logger.warning("配置中未找到实例列表，本轮不处理。")
        return

    now_ts = time.time() # 当前时间戳，用于 increase 计算的结束点

    success_pushes = 0
    failed_pushes = 0
    skipped_instances = 0

    for item in instances:
        if not running:
            logger.info("循环在处理实例期间被中断。")
            break
        if not isinstance(item, dict):
            logger.warning(f"跳过无效的实例配置项（非字典）: {item}")
            skipped_instances += 1
            continue

        instance_id = item.get('identifier')
        reset_day = item.get('reset_day')
        custom_labels = item.get('labels', {})  # 获取自定义标签

        # 验证实例配置
        if not instance_id or not isinstance(instance_id, str) or \
           not reset_day or not isinstance(reset_day, int):
            logger.warning(f"跳过无效的实例配置项（缺少 identifier 或 reset_day）: {item}")
            skipped_instances += 1
            continue

        # 记录新配置项的详细信息
        logger.debug(f"--- 处理实例: {instance_id}, 重置日: {reset_day}, 自定义标签: {custom_labels} ---")

        # 为每个实例的处理添加 try-except 块
        try:
            # 首先推送实例信息指标，包含所有标签
            instance_info_metric = metric_names.get('instance_info', 'instance_info')
            if push_metric(pushgateway_url, push_job_name, instance_id, instance_info_metric,
                          f"Instance information for {instance_id}",
                          1, reset_day, custom_labels):
                success_pushes += 1
            else:
                failed_pushes += 1

            # --- 推送 Monthly Limit (Bytes) ---
            limit_gb = custom_labels.get('monthly_limit_gb')
            if limit_gb is not None:
                try:
                    limit_bytes = float(limit_gb) * 1024 * 1024 * 1024
                    limit_metric_name = metric_names.get('monthly_limit', 'instance_monthly_limit_bytes')

                    if push_metric(pushgateway_url, push_job_name, instance_id, limit_metric_name,
                                   f"Monthly traffic limit in bytes for {instance_id}",
                                   limit_bytes, reset_day, custom_labels):
                        success_pushes += 1
                    else:
                        failed_pushes += 1
                except (ValueError, TypeError):
                    logger.warning(f"无法解析 monthly_limit_gb: {limit_gb} for instance {instance_id}")
            start_ts = get_billing_cycle_start(reset_day)
            if start_ts is None:
                logger.error(f"无法计算实例 {instance_id} 的计费开始时间，跳过。")
                skipped_instances += 1
                continue

            # --- 计算 Increase 指标 ---
            increase_tx = calculate_increase_value(prometheus_url, instance_id, base_metrics['tx'], device_filter_increase, start_ts, now_ts)
            increase_rx = calculate_increase_value(prometheus_url, instance_id, base_metrics['rx'], device_filter_increase, start_ts, now_ts)

            # --- 推送 TX Increase ---
            tx_metric_name = metric_names['tx_increase']
            # 如果计算失败 (返回 None)，则推送时跳过；如果计算结果为 0，则推送 0
            tx_value = int(round(increase_tx)) if increase_tx is not None else None
            if push_metric(pushgateway_url, push_job_name, instance_id, tx_metric_name,
                           f"Bytes transmitted since last reset day (increase) for {instance_id}",
                           tx_value, reset_day, custom_labels):
                success_pushes += 1
            elif tx_value is not None: # 仅当值不是 None 时才算推送失败
                failed_pushes += 1

            # --- 推送 RX Increase ---
            rx_metric_name = metric_names['rx_increase']
            rx_value = int(round(increase_rx)) if increase_rx is not None else None

            if push_metric(pushgateway_url, push_job_name, instance_id, rx_metric_name,
                           f"Bytes received since last reset day (increase) for {instance_id}",
                           rx_value, reset_day, custom_labels):
                success_pushes += 1
            elif rx_value is not None:
                failed_pushes += 1

            # --- 计算并推送 Total Increase ---
            total_increase = None
            # 只有在 TX 和 RX 都成功计算出数值 (不是 None) 时才相加
            if tx_value is not None and rx_value is not None:
                total_increase = tx_value + rx_value
            elif tx_value is not None: # 如果只有 TX 成功
                total_increase = tx_value
            elif rx_value is not None: # 如果只有 RX 成功
                total_increase = rx_value
            # 如果两者都计算失败 (值为 None)，total_increase 保持 None

            total_metric_name = metric_names['total_increase']
            if push_metric(pushgateway_url, push_job_name, instance_id, total_metric_name,
                           f"Total bytes (TX+RX) since last reset day (increase) for {instance_id}",
                           total_increase, reset_day, custom_labels):
                success_pushes += 1
            elif total_increase is not None:
                failed_pushes += 1

        except Exception as e:
            # 捕获处理单个实例时发生的任何未预料的错误
            logger.error(f"处理实例 {instance_id} 时发生未知错误: {e}", exc_info=True) # exc_info=True 会记录堆栈跟踪
            skipped_instances += 1
            # 继续处理下一个实例

        # 实例间短暂休眠，避免瞬间对 API 造成太大压力
        if running: # 只有在还在运行时才休眠
             time.sleep(0.2)

    logger.info(f"本轮处理完成。成功推送: {success_pushes}, 推送失败: {failed_pushes}, 跳过实例: {skipped_instances}")


def main_loop():
    """主循环，定期执行处理"""
    # 加载一次初始配置以获取循环间隔和设置日志
    try:
        initial_config = load_config()
        setup_logging(initial_config) # 使用加载的配置设置日志
        loop_interval = initial_config.get('loop_interval_seconds', DEFAULT_CONFIG['loop_interval_seconds'])
        loop_interval = max(1, loop_interval) # 确保间隔至少是 1 秒
    except Exception as e:
        # 如果初始加载或日志设置失败，记录到 stderr 并尝试继续
        print(f"[{datetime.now()}] CRITICAL - 初始配置加载或日志设置失败: {e}", file=sys.stderr)
        # 使用默认间隔，并尝试设置基本日志到控制台
        loop_interval = DEFAULT_CONFIG['loop_interval_seconds']
        logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
        logger.critical("初始配置加载或日志设置失败，将使用默认循环间隔并尝试继续。")


    while running:
        logger.info("开始新一轮流量计算...")
        # 在循环内部加载最新配置
        current_config = load_config()
        # 检查日志配置是否有变，如果有则重新设置
        # 比较关键的日志配置项
        if current_config.get('logging_enabled') != initial_config.get('logging_enabled') or \
           current_config.get('log_file') != initial_config.get('log_file') or \
           current_config.get('log_level') != initial_config.get('log_level') or \
           current_config.get('log_rotation_enabled') != initial_config.get('log_rotation_enabled') or \
           current_config.get('log_max_bytes') != initial_config.get('log_max_bytes') or \
           current_config.get('log_backup_count') != initial_config.get('log_backup_count'):
            logger.info("检测到日志配置更改，重新设置日志记录器...")
            setup_logging(current_config)
            # 更新 initial_config 中记录的日志设置，避免重复设置
            initial_config['logging_enabled'] = current_config.get('logging_enabled')
            initial_config['log_file'] = current_config.get('log_file')
            initial_config['log_level'] = current_config.get('log_level')
            initial_config['log_rotation_enabled'] = current_config.get('log_rotation_enabled')
            initial_config['log_max_bytes'] = current_config.get('log_max_bytes')
            initial_config['log_backup_count'] = current_config.get('log_backup_count')


        # 只有在日志启用时才处理实例
        if current_config.get('logging_enabled', DEFAULT_CONFIG['logging_enabled']):
             process_instances(current_config)
        else:
             # 如果日志禁用，打印一条 debug 信息（如果 debug 级别启用的话）
             logger.debug("日志记录已禁用，跳过实例处理。")


        # 使用本次循环加载的（或初始的）间隔时间
        current_loop_interval = current_config.get('loop_interval_seconds', loop_interval)
        current_loop_interval = max(1, current_loop_interval) # 确保至少 1 秒

        logger.info(f"本轮计算完成，休眠 {current_loop_interval} 秒...")
        # 使用循环进行休眠，以便能更快地响应停止信号
        for _ in range(current_loop_interval):
            if not running:
                break
            time.sleep(1)

    logger.info("脚本循环结束。")

# --- 程序入口 ---
if __name__ == "__main__":
    # 初始加载配置并设置日志
    initial_config = load_config()
    setup_logging(initial_config) 

    # 注册信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 启动延迟 (给 Pushgateway 等服务一点时间)
    startup_delay = 5
    logger.info(f"流量计算服务将在 {startup_delay} 秒后开始第一个循环...")
    time.sleep(startup_delay)

    logger.info("流量计算服务启动。")
    try:
        main_loop()
    except Exception as e:
        # 捕获主循环中未处理的异常
        logger.critical("主循环发生未捕获的致命错误，服务退出。", exc_info=True)
        sys.exit(1) # 异常退出
    finally:
        # 确保服务停止时记录日志并关闭处理器
        logger.info("流量计算服务已停止。")
        logging.shutdown() # 关闭所有日志处理器

