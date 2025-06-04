#!/usr/bin/env python3
"""
Модуль для сравнения текущей конфигурации оборудования с эталонной (baseline)
"""

import json
import subprocess
import re
from pathlib import Path
from typing import Dict, List, Any, Tuple
from datetime import datetime

# Безопасный импорт функций сбора данных
try:
    from create_baseline_config import get_cpu_info, get_memory_info, get_pci_info, get_usb_info, get_storage_info
    BASELINE_FUNCTIONS_AVAILABLE = True
except ImportError:
    print("⚠️  Модуль create_baseline_config не найден. Используем fallback функции.")
    BASELINE_FUNCTIONS_AVAILABLE = False

def fallback_get_cpu_info():
    """Fallback функция для получения информации о CPU"""
    try:
        result = subprocess.run(['dmidecode', '-t', 'processor'], 
                              capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        
        processors = []
        socket_id = 0
        socket = f'CPU{socket_id}'  # ИСПРАВЛЕНО: инициализируем socket по умолчанию
        
        for line in result.stdout.splitlines():
            if 'Socket Designation:' in line:
                socket = line.split(':', 1)[1].strip()
            elif 'Version:' in line and 'INTEL' in line.upper():
                model = line.split(':', 1)[1].strip()
                processors.append({
                    'socket': socket,
                    'model': model,
                    'cores': 'Unknown',
                    'threads': 'Unknown'
                })
                socket_id += 1
                socket = f'CPU{socket_id}'  # Обновляем socket для следующего процессора
        return processors
    except Exception as e:
        print(f"⚠️  Ошибка сбора CPU info: {e}")
        return []

def fallback_get_memory_info():
    """Fallback функция для получения информации о памяти"""
    try:
        result = subprocess.run(['dmidecode', '-t', 'memory'], 
                              capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        
        memory_modules = []
        current_module = {}
        
        for line in result.stdout.splitlines():
            line = line.strip()
            if 'Locator:' in line and 'Bank' not in line:
                if current_module:
                    memory_modules.append(current_module)
                current_module = {'slot': line.split(':', 1)[1].strip()}
            elif 'Size:' in line:
                size = line.split(':', 1)[1].strip()
                current_module['size'] = size
                current_module['populated'] = 'No Module Installed' not in size
        
        if current_module:
            memory_modules.append(current_module)
        return memory_modules
    except Exception as e:
        print(f"⚠️  Ошибка сбора memory info: {e}")
        return []

def fallback_get_pci_info():
    """Fallback функция для получения информации о PCI"""
    try:
        result = subprocess.run(['lspci', '-v'], 
                              capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        
        devices = []
        for line in result.stdout.splitlines():
            if re.match(r'^[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]', line):
                bdf_desc = line.split(' ', 1)
                if len(bdf_desc) == 2:
                    devices.append({
                        'bdf': bdf_desc[0],
                        'description': bdf_desc[1],
                        'class': bdf_desc[1],
                        'width': 'unknown',
                        'speed': 'unknown'
                    })
        return devices
    except Exception as e:
        print(f"⚠️  Ошибка сбора PCI info: {e}")
        return []

def fallback_get_usb_info():
    """Fallback функция для получения информации о USB"""
    try:
        result = subprocess.run(['lsusb'], 
                              capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        
        devices = []
        for line in result.stdout.splitlines():
            if 'Bus' in line and 'Device' in line:
                parts = line.split()
                if len(parts) >= 6:
                    vid_pid = parts[5]
                    description = ' '.join(parts[6:]) if len(parts) > 6 else 'Unknown'
                    devices.append({
                        'bus': parts[1],
                        'device': parts[3].rstrip(':'),
                        'vid_pid': vid_pid,
                        'description': description,
                        'usb_version': 'USB2.0'
                    })
        return devices
    except Exception as e:
        print(f"⚠️  Ошибка сбора USB info: {e}")
        return []

def fallback_get_storage_info():
    """Fallback функция для получения информации о накопителях"""
    try:
        result = subprocess.run(['lsblk', '-J'], 
                              capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        
        # ИСПРАВЛЕНО: Обработка ошибок JSON
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            print(f"⚠️  Ошибка парсинга JSON от lsblk: {e}")
            return []
        
        devices = []
        
        for device in data.get('blockdevices', []):
            if device.get('type') == 'disk':
                devices.append({
                    'name': device['name'],
                    'model': device.get('model', 'Unknown'),
                    'size': device.get('size', '0B'),
                    'type': 'nvme' if 'nvme' in device['name'] else 'sata',
                    'interface': 'NVMe' if 'nvme' in device['name'] else 'SATA'
                })
        return devices
    except Exception as e:
        print(f"⚠️  Ошибка сбора storage info: {e}")
        return []

# Определяем функции для использования
if BASELINE_FUNCTIONS_AVAILABLE:
    cpu_info_func = get_cpu_info
    memory_info_func = get_memory_info
    pci_info_func = get_pci_info
    usb_info_func = get_usb_info
    storage_info_func = get_storage_info
else:
    cpu_info_func = fallback_get_cpu_info
    memory_info_func = fallback_get_memory_info
    pci_info_func = fallback_get_pci_info
    usb_info_func = fallback_get_usb_info
    storage_info_func = fallback_get_storage_info

class HardwareDiff:
    """Класс для сравнения аппаратной конфигурации с эталоном"""
    
    # Приоритеты статусов для правильной эскалации
    STATUS_PRIORITY = {
        'PASS': 0,
        'WARNING': 1,
        'FAIL': 2,
        'ERROR': 3,
        'UNKNOWN': 4
    }
    
    def __init__(self, baseline_path: str):
        """
        Инициализация с путем к эталонной конфигурацией
        
        Args:
            baseline_path: Путь к JSON файлу с эталонной конфигурацией
        """
        self.baseline_path = Path(baseline_path)
        self.baseline_config = self._load_baseline()
        self.diff_results = {}
        self.overall_status = 'UNKNOWN'
        
    def _load_baseline(self) -> Dict:
        """Загрузка эталонной конфигурации"""
        if not self.baseline_path.exists():
            raise FileNotFoundError(f'Эталонная конфигурация не найдена: {self.baseline_path}')
        
        with self.baseline_path.open('r', encoding='utf-8') as f:
            return json.load(f)
    
    def collect_current_config(self) -> Dict:
        """Сбор текущей конфигурации системы"""
        print("📊 Сбор текущей конфигурации системы...")
        
        # Добавляем функцию для сбора данных о райзерах
        def get_riser_info():
            """Получение информации о райзерах через FRU и PCI топологию"""
            risers = []
            
            # Сканируем FRU записи в поисках райзеров
            for fru_id in range(1, 10):  # Обычно FRU ID 1-9 для периферийных устройств
                try:
                    cmd = ['ipmitool', 'fru', 'print', str(fru_id)]
                    # Для безопасности логируем команду без паролей
                    print(f"🔍 Выполнение команды: {' '.join(cmd)}")
                    
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                    
                    if result.returncode == 0 and 'Product Name' in result.stdout:
                        fru_data = {}
                        for line in result.stdout.splitlines():
                            if ':' in line:
                                key, value = line.split(':', 1)
                                key = key.strip()
                                value = value.strip()
                                
                                if 'Product Name' in key:
                                    fru_data['fru_product_name'] = value
                                elif 'Product Manufacturer' in key:
                                    fru_data['fru_manufacturer'] = value
                                elif 'Product Part Number' in key:
                                    fru_data['fru_part_number'] = value
                                elif 'Product Serial' in key:
                                    fru_data['fru_serial_number'] = value
                        
                        # Проверяем, является ли это райзером
                        product_name = fru_data.get('fru_product_name', '').upper()
                        if 'RISER' in product_name or 'RSMB-MS93' in product_name:
                            # Определяем слот райзера из Product Name
                            if 'RISER-1' in product_name:
                                slot = 'RISER_SLOT_1'
                            elif 'RISER-2' in product_name:
                                slot = 'RISER_SLOT_2'
                            elif 'RISER-3' in product_name:
                                slot = 'RISER_SLOT_3'
                            else:
                                slot = f'RISER_SLOT_{fru_id}'
                            
                            riser_info = {
                                'slot': slot,
                                'fru_id': fru_id,
                                'populated': True,
                                **fru_data,
                                'pcie_slots': []  # PCIe слоты определяются через другие методы
                            }
                            risers.append(riser_info)
                            
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    continue
                except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
                    # Логируем специфические ошибки для отладки
                    print(f"⚠️  Ошибка при сканировании FRU {fru_id}: {type(e).__name__}: {e}")
                    continue
            
            # Если не нашли райзеры через FRU, анализируем PCI топологию
            if not risers:
                try:
                    # Получаем дерево PCI
                    result = subprocess.run(['lspci', '-t'], capture_output=True, text=True, timeout=10)
                    if result.returncode == 0:
                        # Анализируем PCI мосты для определения потенциальных райзеров
                        pci_bridges = []
                        for line in result.stdout.splitlines():
                            # Ищем мосты, которые могут быть связаны с райзерами
                            if '-[' in line and ']' in line:
                                # Извлекаем BDF адреса мостов
                                bridge_match = re.search(r'(\w+:\w+\.\w+)', line)
                                if bridge_match:
                                    pci_bridges.append(bridge_match.group(1))
                        
                        # Получаем детальную информацию о мостах
                        bridge_details = []
                        try:
                            result = subprocess.run(['lspci', '-v'], capture_output=True, text=True, timeout=10)
                            if result.returncode == 0:
                                for line in result.stdout.splitlines():
                                    for bridge in pci_bridges:
                                        if bridge in line and 'bridge' in line.lower():
                                            bridge_details.append(line)
                        except Exception:
                            pass
                        
                        # Создаем записи на основе найденных мостов
                        for i, bridge in enumerate(bridge_details[:3]):  # Максимум 3 райзера
                            riser_info = {
                                'slot': f'RISER_SLOT_{i+1}',
                                'populated': True,  # Если есть мост, вероятно есть райзер
                                'fru_product_name': f'Detected via PCI bridge ({bridge.split()[0]})',
                                'fru_manufacturer': 'Detected from PCI topology',
                                'fru_part_number': 'Unknown (no FRU data)',
                                'fru_serial_number': 'Not available via PCI',
                                'pcie_slots': [],
                                'detection_method': 'pci_topology'
                            }
                            risers.append(riser_info)
                            
                except Exception as e:
                    # Если анализ PCI не удался, создаем минимальную структуру
                    print(f"⚠️  Ошибка при анализе PCI топологии: {type(e).__name__}: {e}")
                    pass
            
            # Если всё равно ничего не нашли, создаем placeholder
            if not risers:
                risers = [
                    {
                        'slot': 'RISER_SLOT_1',
                        'populated': False,
                        'fru_product_name': 'Not detected via FRU or PCI',
                        'fru_manufacturer': 'Unknown',
                        'fru_part_number': 'Unknown',
                        'fru_serial_number': 'Not available',
                        'pcie_slots': [],
                        'detection_method': 'placeholder'
                    }
                ]
            
            return risers
        
        current_config = {
            'scan_date': datetime.now().strftime('%Y-%m-%d'),
            'processors': cpu_info_func(),
            'memory_modules': memory_info_func(), 
            'pci_devices': pci_info_func(),
            'usb_devices': usb_info_func(),
            'storage_devices': storage_info_func(),
            'riser_cards': get_riser_info()
        }
        
        print(f"✅ Текущая конфигурация собрана:")
        print(f"├─ Процессоры: {len(current_config['processors'])}")
        print(f"├─ Модули памяти: {len(current_config['memory_modules'])}")
        print(f"├─ PCI устройства: {len(current_config['pci_devices'])}")
        print(f"├─ USB устройства: {len(current_config['usb_devices'])}")
        print(f"├─ Накопители: {len(current_config['storage_devices'])}")
        print(f"└─ Райзеры: {len(current_config['riser_cards'])}")
        
        return current_config
    
    def compare_processors(self, current: List[Dict], baseline: List[Dict]) -> Dict:
        """Сравнение процессоров (CPU socket, id, cores, threads)"""
        diff_result = {
            'status': 'PASS',
            'differences': [],
            'summary': {},
            'details': {
                'current_count': len(current),
                'baseline_count': len(baseline),
                'socket_comparison': []
            }
        }
        
        # Проверка количества процессоров
        if len(current) != len(baseline):
            diff_result['status'] = self._escalate_status(diff_result['status'], 'FAIL')
            diff_result['differences'].append(
                f'CPU count mismatch: current={len(current)}, baseline={len(baseline)}'
            )
        
        # Сравнение по сокетам
        baseline_by_socket = {cpu['socket']: cpu for cpu in baseline}
        current_by_socket = {cpu['socket']: cpu for cpu in current}
        
        for socket_name in set(baseline_by_socket.keys()) | set(current_by_socket.keys()):
            socket_diff = {'socket': socket_name}
            
            if socket_name not in current_by_socket:
                socket_diff['status'] = 'MISSING'
                socket_diff['issue'] = f'CPU socket {socket_name} missing in current system'
                diff_result['differences'].append(socket_diff['issue'])
                diff_result['status'] = self._escalate_status(diff_result['status'], 'FAIL')
                    
            elif socket_name not in baseline_by_socket:
                socket_diff['status'] = 'EXTRA'
                socket_diff['issue'] = f'Extra CPU socket {socket_name} in current system'
                diff_result['differences'].append(socket_diff['issue'])
                diff_result['status'] = self._escalate_status(diff_result['status'], 'WARNING')
                    
            else:
                # Сравниваем характеристики CPU
                current_cpu = current_by_socket[socket_name]
                baseline_cpu = baseline_by_socket[socket_name]
                socket_diff['status'] = 'MATCH'
                
                # Модель процессора
                if current_cpu.get('model') != baseline_cpu.get('model'):
                    socket_diff['model_diff'] = {
                        'current': current_cpu.get('model'),
                        'baseline': baseline_cpu.get('model')
                    }
                    diff_result['differences'].append(
                        f'CPU {socket_name} model mismatch: {current_cpu.get("model")} vs {baseline_cpu.get("model")}'
                    )
                    diff_result['status'] = self._escalate_status(diff_result['status'], 'FAIL')
                
                # Сравнение количества ядер
                if current_cpu.get('cores') != baseline_cpu.get('cores'):
                    # Особая обработка для случая когда fallback возвращает 'Unknown'
                    current_cores = current_cpu.get('cores')
                    baseline_cores = baseline_cpu.get('cores')
                    
                    # Если текущие данные неизвестны, но baseline есть - это предупреждение, а не ошибка
                    if current_cores == 'Unknown' and isinstance(baseline_cores, (int, str)) and baseline_cores != 'Unknown':
                        difference = {
                            'type': 'cores_detection_failed',
                            'description': f"Не удалось определить количество ядер CPU {socket_name} (fallback method)",
                            'current': current_cores,
                            'baseline': baseline_cores,
                            'severity': 'minor'
                        }
                        result['differences'].append(difference)
                        result['status'] = self._escalate_status(result['status'], 'WARNING')
                    # Если оба Unknown - пропускаем сравнение
                    elif current_cores == 'Unknown' and baseline_cores == 'Unknown':
                        pass  # Оба неизвестны - не сравниваем
                    # Если baseline Unknown, а current известен - обновляем baseline (информационно)
                    elif baseline_cores == 'Unknown' and current_cores != 'Unknown':
                        pass  # Не создаем ошибку
                    # Только если оба значения определены и различаются - создаем FAIL
                    else:
                        difference = {
                            'type': 'cores_mismatch',
                            'description': f"CPU {socket_name}: количество ядер изменилось: {current_cores} vs {baseline_cores}",
                            'current': current_cores,
                            'baseline': baseline_cores,
                            'severity': 'major'
                        }
                        result['differences'].append(difference)
                        result['status'] = self._escalate_status(result['status'], 'FAIL')
                
                # Количество потоков
                if current_cpu.get('threads') != baseline_cpu.get('threads'):
                    socket_diff['threads_diff'] = {
                        'current': current_cpu.get('threads'),
                        'baseline': baseline_cpu.get('threads')
                    }
                    diff_result['differences'].append(
                        f'CPU {socket_name} threads mismatch: {current_cpu.get("threads")} vs {baseline_cpu.get("threads")}'
                    )
                    diff_result['status'] = self._escalate_status(diff_result['status'], 'FAIL')
            
            diff_result['details']['socket_comparison'].append(socket_diff)
        
        # Сводка
        diff_result['summary'] = {
            'total_differences': len(diff_result['differences']),
            'cpu_sockets_current': len(current),
            'cpu_sockets_baseline': len(baseline),
            'status_description': 'Процессоры соответствуют эталону' if diff_result['status'] == 'PASS' else 'Обнаружены различия в процессорах'
        }
        
        return diff_result
    
    def compare_memory(self, current: List[Dict], baseline: List[Dict]) -> Dict:
        """Сравнение конфигурации памяти"""
        result = {
            'status': 'PASS',
            'differences': [],
            'summary': {
                'total_differences': 0,
                'memory_slots_populated_current': 0,
                'memory_slots_populated_baseline': 0,
                'total_memory_current_gb': 0,
                'total_memory_baseline_gb': 0,
                'status_description': ''
            },
            'details': {
                'current_slots': 0,
                'baseline_slots': 0,
                'slot_comparison': []
            }
        }
        
        def parse_memory_size(size_str: str) -> int:
            """Безопасный парсинг размера памяти в GB"""
            if not size_str or size_str == 'No Module Installed':
                return 0
            try:
                # Убираем лишние пробелы и приводим к верхнему регистру
                size_str = size_str.strip().upper()
                
                # Если строка заканчивается на GB
                if size_str.endswith(' GB'):
                    return int(size_str.replace(' GB', ''))
                elif size_str.endswith('GB'):
                    return int(size_str.replace('GB', ''))
                # Если строка заканчивается на MB, конвертируем в GB
                elif size_str.endswith(' MB'):
                    mb_value = int(size_str.replace(' MB', ''))
                    return mb_value // 1024  # Конвертация MB в GB
                elif size_str.endswith('MB'):
                    mb_value = int(size_str.replace('MB', ''))
                    return mb_value // 1024
                # Если просто число - считаем что это GB
                elif size_str.isdigit():
                    return int(size_str)
                else:
                    # Пробуем извлечь первое число из строки
                    import re
                    numbers = re.findall(r'\d+', size_str)
                    if numbers:
                        return int(numbers[0])
                    return 0
            except (ValueError, IndexError) as e:
                print(f"⚠️  Ошибка парсинга размера памяти '{size_str}': {e}")
                return 0
        
        # Подсчитываем текущую и базовую конфигурацию
        for dimm in current:
            if dimm.get('populated', False):
                result['summary']['memory_slots_populated_current'] += 1
                size_gb = parse_memory_size(dimm.get('size', ''))
                result['summary']['total_memory_current_gb'] += size_gb
        
        for dimm in baseline:
            if dimm.get('populated', False):
                result['summary']['memory_slots_populated_baseline'] += 1
                size_gb = parse_memory_size(dimm.get('size', ''))
                result['summary']['total_memory_baseline_gb'] += size_gb
        
        result['details']['current_slots'] = result['summary']['total_memory_current_gb']
        result['details']['baseline_slots'] = result['summary']['total_memory_baseline_gb']
        
        # Сравниваем количество заселённых слотов
        if result['summary']['memory_slots_populated_current'] != result['summary']['memory_slots_populated_baseline']:
            difference = {
                'type': 'slot_count_mismatch',
                'description': f"Количество заселённых слотов памяти изменилось: {result['summary']['memory_slots_populated_current']} vs {result['summary']['memory_slots_populated_baseline']}",
                'severity': 'warning'
            }
            result['differences'].append(difference)
            result['status'] = self._escalate_status(result['status'], 'WARNING')
        
        # Сравниваем общий объём памяти
        if result['summary']['total_memory_current_gb'] != result['summary']['total_memory_baseline_gb']:
            difference = {
                'type': 'total_memory_mismatch',
                'description': f"Общий объём памяти изменился: {result['summary']['total_memory_current_gb']}GB vs {result['summary']['total_memory_baseline_gb']}GB",
                'severity': 'major'
            }
            result['differences'].append(difference)
            result['status'] = self._escalate_status(result['status'], 'FAIL')
        
        # Создаём список сравнения слотов
        current_slots = {dimm.get('slot'): dimm for dimm in current}
        baseline_slots = {dimm.get('slot'): dimm for dimm in baseline}
        
        all_slots = set(current_slots.keys()) | set(baseline_slots.keys())
        
        for slot in sorted(all_slots):
            current_dimm = current_slots.get(slot, {})
            baseline_dimm = baseline_slots.get(slot, {})
            
            current_populated = current_dimm.get('populated', False)
            baseline_populated = baseline_dimm.get('populated', False)
            
            if current_populated == baseline_populated:
                if current_populated:
                    result['details']['slot_comparison'].append({
                        'slot': slot,
                        'status': 'POPULATED'
                    })
                else:
                    result['details']['slot_comparison'].append({
                        'slot': slot,
                        'status': 'EMPTY'
                    })
            else:
                # Слот изменил статус заселённости
                if current_populated:
                    status_text = 'ADDED'
                else:
                    status_text = 'REMOVED'
                
                result['details']['slot_comparison'].append({
                    'slot': slot,
                    'status': status_text
                })
                
                difference = {
                    'type': 'slot_population_change',
                    'slot': slot,
                    'description': f"Слот {slot}: {status_text}",
                    'severity': 'minor'
                }
                result['differences'].append(difference)
                result['status'] = self._escalate_status(result['status'], 'WARNING')
        
        # Устанавливаем итоговое описание
        result['summary']['total_differences'] = len(result['differences'])
        
        if result['status'] == 'PASS':
            result['summary']['status_description'] = 'Память соответствует эталону'
        elif result['status'] == 'WARNING':
            result['summary']['status_description'] = 'Обнаружены незначительные различия в конфигурации памяти'
        else:
            result['summary']['status_description'] = 'Обнаружены критические различия в конфигурации памяти'
        
        return result
    
    def compare_pci_devices(self, current: List[Dict], baseline: List[Dict]) -> Dict:
        """Сравнение PCIe устройств (BDF, device, class, width, speed)"""
        diff_result = {
            'status': 'PASS',
            'differences': [],
            'summary': {},
            'details': {
                'current_count': len(current),
                'baseline_count': len(baseline),
                'critical_devices_check': [],
                'device_comparison': []
            }
        }
        
        # Создаем словари по BDF адресам
        baseline_by_bdf = {dev['bdf']: dev for dev in baseline}
        current_by_bdf = {dev['bdf']: dev for dev in current}
        
        # Определяем критические классы устройств
        critical_classes = {
            'Host bridge', 'PCI bridge', 'ISA bridge', 'Ethernet controller', 
            'USB controller', 'SATA controller', 'System peripheral'
        }
        
        # ИСПРАВЛЕНО: функция для очистки class от квадратных скобок и кодов
        def clean_device_class(device_class: str) -> str:
            """Очищает class от квадратных скобок типа [0200] для корректного сравнения"""
            if not device_class:
                return 'Unknown'
            # Убираем всё что в квадратных скобках: "Ethernet controller [0200]" -> "Ethernet controller"
            return device_class.split(' [')[0].strip()
        
        # Проверяем критические устройства - ИСПРАВЛЕНО: используем clean_device_class для обеих сторон
        baseline_critical = {
            bdf: dev for bdf, dev in baseline_by_bdf.items() 
            if any(cls in clean_device_class(dev.get('class', '')) for cls in critical_classes)
        }
        current_critical = {
            bdf: dev for bdf, dev in current_by_bdf.items() 
            if any(cls in clean_device_class(dev.get('class', '')) for cls in critical_classes)
        }
        
        # Группируем критические устройства по классам - ИСПРАВЛЕНО: используем clean_device_class
        baseline_by_class = {}
        for dev in baseline_critical.values():
            dev_class = clean_device_class(dev.get('class', 'Unknown'))
            if dev_class not in baseline_by_class:
                baseline_by_class[dev_class] = []
            baseline_by_class[dev_class].append(dev)
        
        current_by_class = {}
        for dev in current_critical.values():
            dev_class = clean_device_class(dev.get('class', 'Unknown'))
            if dev_class not in current_by_class:
                current_by_class[dev_class] = []
            current_by_class[dev_class].append(dev)
        
        # Сравниваем критические устройства по классам
        for dev_class in baseline_by_class:
            baseline_count = len(baseline_by_class[dev_class])
            current_count = len(current_by_class.get(dev_class, []))
            
            class_check = {
                'class': dev_class,
                'baseline_count': baseline_count,
                'current_count': current_count,
                'status': 'MATCH' if baseline_count == current_count else 'MISMATCH'
            }
            
            if baseline_count != current_count:
                class_check['issue'] = f'{dev_class}: expected {baseline_count}, found {current_count}'
                diff_result['differences'].append(class_check['issue'])
                diff_result['status'] = self._escalate_status(diff_result['status'], 'FAIL')
            
            diff_result['details']['critical_devices_check'].append(class_check)
        
        # Проверяем новые критические классы устройств
        for dev_class in current_by_class:
            if dev_class not in baseline_by_class:
                class_check = {
                    'class': dev_class,
                    'baseline_count': 0,
                    'current_count': len(current_by_class[dev_class]),
                    'status': 'NEW',
                    'issue': f'New device class detected: {dev_class}'
                }
                diff_result['differences'].append(class_check['issue'])
                diff_result['status'] = self._escalate_status(diff_result['status'], 'WARNING')
                diff_result['details']['critical_devices_check'].append(class_check)
        
        # Детальная проверка конкретных BDF адресов (только для критических)
        for bdf in set(baseline_critical.keys()) | set(current_critical.keys()):
            device_diff = {'bdf': bdf}
            
            if bdf not in current_by_bdf:
                device_diff['status'] = 'MISSING'
                device_diff['issue'] = f'Critical device {bdf} missing'
                diff_result['differences'].append(device_diff['issue'])
                diff_result['status'] = self._escalate_status(diff_result['status'], 'FAIL')
                    
            elif bdf not in baseline_by_bdf:
                device_diff['status'] = 'EXTRA'
                device_diff['description'] = current_by_bdf[bdf].get('description', 'Unknown')
                # Дополнительные устройства не критичны
                    
            else:
                current_dev = current_by_bdf[bdf]
                baseline_dev = baseline_by_bdf[bdf]
                device_diff['status'] = 'PRESENT'
                
                # Проверяем изменения в описании устройства
                if current_dev.get('description') != baseline_dev.get('description'):
                    device_diff['description_diff'] = {
                        'current': current_dev.get('description'),
                        'baseline': baseline_dev.get('description')
                    }
                    # ИСПРАВЛЕНО: используем clean_device_class для сравнения критичности
                    baseline_clean_class = clean_device_class(baseline_dev.get('class', ''))
                    if any(cls in baseline_clean_class for cls in ['Ethernet controller', 'USB controller']):
                        diff_result['differences'].append(
                            f'Critical device {bdf} description changed: {current_dev.get("description")} vs {baseline_dev.get("description")}'
                        )
                        diff_result['status'] = self._escalate_status(diff_result['status'], 'WARNING')
            
            if device_diff.get('status') in ['MISSING', 'EXTRA', 'PRESENT']:
                diff_result['details']['device_comparison'].append(device_diff)
        
        # Сводка
        diff_result['summary'] = {
            'total_differences': len(diff_result['differences']),
            'pci_devices_current': len(current),
            'pci_devices_baseline': len(baseline),
            'critical_devices_current': len(current_critical),
            'critical_devices_baseline': len(baseline_critical),
            'status_description': 'PCIe устройства соответствуют эталону' if diff_result['status'] == 'PASS' else 'Обнаружены различия в PCIe устройствах'
        }
        
        return diff_result
    
    def compare_usb_devices(self, current: List[Dict], baseline: List[Dict]) -> Dict:
        """Сравнение USB устройств (bus, dev, VID:PID)"""
        diff_result = {
            'status': 'PASS',
            'differences': [],
            'summary': {},
            'details': {
                'current_count': len(current),
                'baseline_count': len(baseline),
                'hub_comparison': [],
                'device_comparison': []
            }
        }
        
        # Список VID:PID устройств, которые можно игнорировать (KVM, temporary devices)
        IGNORABLE_DEVICES = {
            '0557:8021',  # ATEN KVM Hub
            '046b:ff01',  # AMI Virtual Hub
            '046b:ff20',  # AMI Virtual CDROM
            '046b:ff31',  # AMI Virtual HDisk
            '046b:ff10',  # AMI Virtual Keyboard/Mouse
            '046b:ffb0',  # AMI Virtual Ethernet
            '0557:223a'   # ATEN CS1316 KVM Switch
        }
        
        # Фильтруем USB хабы и контроллеры (критические)
        def is_critical_usb(device):
            """Определяет критичность USB устройства"""
            description = device.get('description', '').lower()
            vendor = device.get('vendor', '').lower()
            
            # USB хабы и контроллеры - критичные
            if any(keyword in description for keyword in [
                'hub', 'root hub', 'host controller', 'xhci', 'ehci', 'ohci', 'uhci'
            ]):
                return True
            
            # Системные USB устройства
            if any(keyword in description for keyword in [
                'keyboard', 'mouse', 'management', 'controller'
            ]):
                return True
            
            # Известные критичные вендоры
            if any(vendor_name in vendor for vendor_name in [
                'intel', 'amd', 'via', 'nvidia'
            ]):
                return True
            
            return False
        
        baseline_critical = [dev for dev in baseline if is_critical_usb(dev)]
        current_critical = [dev for dev in current if is_critical_usb(dev)]
        
        # Сравниваем USB хабы по VID:PID
        baseline_hubs_by_vid_pid = {}
        for dev in baseline_critical:
            vid_pid = dev.get('vid_pid', '')
            if vid_pid not in baseline_hubs_by_vid_pid:
                baseline_hubs_by_vid_pid[vid_pid] = []
            baseline_hubs_by_vid_pid[vid_pid].append(dev)
        
        current_hubs_by_vid_pid = {}
        for dev in current_critical:
            vid_pid = dev.get('vid_pid', '')
            if vid_pid not in current_hubs_by_vid_pid:
                current_hubs_by_vid_pid[vid_pid] = []
            current_hubs_by_vid_pid[vid_pid].append(dev)
        
        # Проверяем критические USB устройства
        for vid_pid in set(baseline_hubs_by_vid_pid.keys()) | set(current_hubs_by_vid_pid.keys()):
            baseline_count = len(baseline_hubs_by_vid_pid.get(vid_pid, []))
            current_count = len(current_hubs_by_vid_pid.get(vid_pid, []))
            
            hub_check = {
                'vid_pid': vid_pid,
                'baseline_count': baseline_count,
                'current_count': current_count,
                'status': 'MATCH' if baseline_count == current_count else 'MISMATCH'
            }
            
            if baseline_count != current_count:
                if baseline_count > 0:
                    baseline_desc = baseline_hubs_by_vid_pid[vid_pid][0].get('description', vid_pid)
                else:
                    baseline_desc = 'Unknown'
                    
                hub_check['issue'] = f'USB device {vid_pid} ({baseline_desc}): expected {baseline_count}, found {current_count}'
                diff_result['differences'].append(hub_check['issue'])
                
                # Отсутствие критических USB устройств - FAIL
                if baseline_count > current_count:
                    diff_result['status'] = self._escalate_status(diff_result['status'], 'FAIL')
                # Дополнительные устройства - WARNING
                elif current_count > baseline_count:
                    diff_result['status'] = self._escalate_status(diff_result['status'], 'WARNING')
            
            diff_result['details']['hub_comparison'].append(hub_check)
        
        # Добавляем информацию об игнорируемых устройствах в отчет
        ignored_devices = []
        for device in current:
            if device.get('vid_pid') in IGNORABLE_DEVICES:
                ignored_devices.append(device)
        
        if ignored_devices:
            diff_result['details']['ignored_devices'] = ignored_devices
            diff_result['details']['ignored_count'] = len(ignored_devices)
        
        # Сводка
        diff_result['summary'] = {
            'total_differences': len(diff_result['differences']),
            'usb_devices_current': len(current),
            'usb_devices_baseline': len(baseline),
            'critical_usb_current': len(current_critical),
            'critical_usb_baseline': len(baseline_critical),
            'ignored_devices_count': len(ignored_devices) if ignored_devices else 0,
            'status_description': 'USB устройства соответствуют эталону' if diff_result['status'] == 'PASS' else 'Обнаружены различия в USB устройствах'
        }
        
        return diff_result
    
    def compare_storage_devices(self, current: List[Dict], baseline: List[Dict]) -> Dict:
        """Сравнение устройств хранения"""
        
        # Фильтруем виртуальные диски BMC
        def filter_virtual_devices(devices):
            filtered = []
            for device in devices:
                model = device.get('model', '').lower()
                # Исключаем виртуальные диски BMC
                if 'virtual hdisk' not in model and 'ami virtual' not in model:
                    filtered.append(device)
            return filtered
        
        # ИСПРАВЛЕНО: Применяем фильтрацию к обеим сторонам одинаково
        current_filtered = filter_virtual_devices(current)
        baseline_filtered = filter_virtual_devices(baseline)
        
        def group_by_type(devices):
            """Группировка устройств по типу с улучшенной классификацией"""
            groups = {
                'nvme': [],
                'sata': [], 
                'sas': [],
                'mmc': [],
                'usb': [],
                'raid': [],
                'other': []
            }
            
            for device in devices:
                device_name = device.get('device', '').lower()
                model = device.get('model', '').lower()
                transport = device.get('transport', '').lower()
                
                # NVMe устройства
                if device_name.startswith('nvme') or 'nvme' in model:
                    groups['nvme'].append(device)
                # SAS устройства (определяем по модели или транспорту)
                elif 'sas' in model or 'sas' in transport or any(sas_vendor in model for sas_vendor in ['seagate', 'hitachi', 'toshiba']) and 'sas' in model:
                    groups['sas'].append(device)
                # RAID контроллеры и виртуальные диски
                elif any(raid_keyword in model for raid_keyword in ['raid', 'logical', 'virtual', 'megaraid', 'adaptec']):
                    groups['raid'].append(device)
                # SATA устройства (включая SATA SSD)
                elif (device_name.startswith('sd') and 
                      any(sata_keyword in model for sata_keyword in ['sata', 'ata', 'ssd']) or 
                      'sata' in transport):
                    groups['sata'].append(device)
                # eMMC/MMC устройства
                elif device_name.startswith('mmcblk') or 'mmc' in model:
                    groups['mmc'].append(device)
                # USB устройства
                elif 'usb' in transport or 'usb' in model:
                    groups['usb'].append(device)
                # SCSI диски без явного типа - пытаемся определить по размеру/модели
                elif device_name.startswith('sd'):
                    # Если это большой диск (>100GB) и не SSD - скорее всего SAS
                    try:
                        size_str = device.get('size', '0')
                        if 'GB' in size_str:
                            size_gb = float(size_str.replace('GB', '').strip())
                            if size_gb > 100 and 'ssd' not in model:
                                groups['sas'].append(device)
                            else:
                                groups['sata'].append(device)
                        else:
                            groups['sata'].append(device)  # По умолчанию SATA
                    except (ValueError, TypeError):
                        groups['sata'].append(device)  # По умолчанию SATA при ошибке парсинга
                else:
                    groups['other'].append(device)
            
            return groups
        
        current_by_type = group_by_type(current_filtered)
        baseline_by_type = group_by_type(baseline_filtered)
        
        differences = []
        
        # Проверяем каждый тип устройств
        all_types = set(current_by_type.keys()) | set(baseline_by_type.keys())
        
        for storage_type in all_types:
            current_devices = current_by_type.get(storage_type, [])
            baseline_devices = baseline_by_type.get(storage_type, [])
            
            if len(current_devices) != len(baseline_devices):
                differences.append(
                    f"{storage_type.upper()} count mismatch: "
                    f"current={len(current_devices)}, baseline={len(baseline_devices)}"
                )
            
            # ИСПРАВЛЕНО: Проверяем модели без использования zip для избежания пропуска лишних
            max_devices = max(len(current_devices), len(baseline_devices))
            for i in range(max_devices):
                curr = current_devices[i] if i < len(current_devices) else None
                base = baseline_devices[i] if i < len(baseline_devices) else None
                
                if curr is None:
                    differences.append(
                        f"{storage_type.upper()} {i+1}: missing in current (baseline has '{base.get('model', 'Unknown')}')"
                    )
                elif base is None:
                    differences.append(
                        f"{storage_type.upper()} {i+1}: extra in current ('{curr.get('model', 'Unknown')}')"
                    )
                else:
                    curr_model = curr.get('model', 'Unknown')
                    base_model = base.get('model', 'Unknown')
                    
                    if curr_model != base_model:
                        differences.append(
                            f"{storage_type.upper()} {i+1} model mismatch: "
                            f"current='{curr_model}', baseline='{base_model}'"
                        )
        
        # ИСПРАВЛЕНО: Убедимся что status всегда есть
        status = 'PASS' if not differences else 'FAIL'
        
        # Подсчитываем отфильтрованные устройства
        virtual_devices_current = len(current) - len(current_filtered)
        virtual_devices_baseline = len(baseline) - len(baseline_filtered)
        
        return {
            'component': 'storage_devices',
            'status': status,  # ИСПРАВЛЕНО: status всегда присутствует
            'current_count': len(current_filtered),
            'baseline_count': len(baseline_filtered),
            'current_by_type': {k: len(v) for k, v in current_by_type.items()},
            'baseline_by_type': {k: len(v) for k, v in baseline_by_type.items()},
            'virtual_devices_filtered': {
                'current': virtual_devices_current,
                'baseline': virtual_devices_baseline
            },
            'differences': differences
        }

    def compare_riser_cards(self, current: List[Dict], baseline: List[Dict]) -> Dict:
        """Сравнение райзеров (FRU проверка) согласно TRD 4.2.6.3.6"""
        differences = []
        
        # Создаем словари для быстрого поиска по слотам
        current_by_slot = {riser.get('slot', ''): riser for riser in current}
        baseline_by_slot = {riser.get('slot', ''): riser for riser in baseline}
        
        all_slots = set(current_by_slot.keys()) | set(baseline_by_slot.keys())
        
        populated_current = 0
        populated_baseline = 0
        
        for slot in sorted(all_slots):
            current_riser = current_by_slot.get(slot)
            baseline_riser = baseline_by_slot.get(slot)
            
            # Проверка наличия райзера в слоте
            if current_riser is None:
                differences.append(f"Slot {slot}: Missing in current configuration")
                continue
            elif baseline_riser is None:
                differences.append(f"Slot {slot}: Extra riser found (not in baseline)")
                continue
            
            # Проверка состояния заполненности
            curr_populated = current_riser.get('populated', False)
            base_populated = baseline_riser.get('populated', False)
            
            if curr_populated:
                populated_current += 1
            if base_populated:
                populated_baseline += 1
            
            if curr_populated != base_populated:
                differences.append(
                    f"Slot {slot}: Population status mismatch - "
                    f"current={'populated' if curr_populated else 'empty'}, "
                    f"baseline={'populated' if base_populated else 'empty'}"
                )
                continue
            
            # Если райзер установлен в обеих конфигурациях, проверяем FRU данные
            if curr_populated and base_populated:
                # Проверка FRU Product Name
                curr_product = current_riser.get('fru_product_name', '')
                base_product = baseline_riser.get('fru_product_name', '')
                if curr_product != base_product:
                    differences.append(
                        f"Slot {slot}: FRU Product Name mismatch - "
                        f"current='{curr_product}', baseline='{base_product}'"
                    )
                
                # Проверка FRU Manufacturer
                curr_manufacturer = current_riser.get('fru_manufacturer', '')
                base_manufacturer = baseline_riser.get('fru_manufacturer', '')
                if curr_manufacturer != base_manufacturer:
                    differences.append(
                        f"Slot {slot}: FRU Manufacturer mismatch - "
                        f"current='{curr_manufacturer}', baseline='{base_manufacturer}'"
                    )
                
                # Проверка FRU Part Number
                curr_part = current_riser.get('fru_part_number', '')
                base_part = baseline_riser.get('fru_part_number', '')
                if curr_part != base_part:
                    differences.append(
                        f"Slot {slot}: FRU Part Number mismatch - "
                        f"current='{curr_part}', baseline='{base_part}'"
                    )
                
                # Проверка наличия серийного номера (обязательное поле)
                curr_serial = current_riser.get('fru_serial_number', '')
                if not curr_serial or curr_serial == 'Required':
                    differences.append(
                        f"Slot {slot}: FRU Serial Number missing or invalid - "
                        f"current='{curr_serial}'"
                    )
                
                # Проверка PCIe слотов на райзере
                curr_pcie_slots = current_riser.get('pcie_slots', [])
                base_pcie_slots = baseline_riser.get('pcie_slots', [])
                
                if len(curr_pcie_slots) != len(base_pcie_slots):
                    differences.append(
                        f"Slot {slot}: PCIe slots count mismatch - "
                        f"current={len(curr_pcie_slots)}, baseline={len(base_pcie_slots)}"
                    )
        
        # Общая проверка количества заполненных слотов
        if populated_current != populated_baseline:
            differences.append(
                f"Total populated risers mismatch: "
                f"current={populated_current}, baseline={populated_baseline}"
            )
        
        # ИСПРАВЛЕНО: Более четкое определение критичности различий
        critical_differences = []
        for diff in differences:
            # Критические случаи
            if any(keyword in diff.lower() for keyword in [
                'missing in current', 'fru serial number missing', 
                'populated risers mismatch', 'population status mismatch'
            ]):
                critical_differences.append(diff)
        
        if critical_differences:
            status = 'FAIL'
        elif differences:
            status = 'WARNING'  
        else:
            status = 'PASS'
        
        return {
            'component': 'riser_cards',
            'status': status,
            'current_populated': populated_current,
            'baseline_populated': populated_baseline,
            'current_slots': len(current),
            'baseline_slots': len(baseline),
            'differences': differences,
            'critical_differences': critical_differences
        }
    
    def perform_full_diff(self) -> Dict:
        """Выполнение полного сравнения с эталоном"""
        print("🔍 Запуск полного HW-Diff с эталоном...")
        
        # Собираем текущую конфигурацию
        current_config = self.collect_current_config()
        
        # Выполняем сравнения по компонентам
        print("├─ Сравнение процессоров...")
        cpu_diff = self.compare_processors(
            current_config['processors'], 
            self.baseline_config['processors']
        )
        
        print("├─ Сравнение памяти...")
        memory_diff = self.compare_memory(
            current_config['memory_modules'], 
            self.baseline_config['memory_modules']
        )
        
        print("├─ Сравнение PCIe устройств...")
        pci_diff = self.compare_pci_devices(
            current_config['pci_devices'], 
            self.baseline_config['pci_devices']
        )
        
        print("├─ Сравнение USB устройств...")
        usb_diff = self.compare_usb_devices(
            current_config['usb_devices'], 
            self.baseline_config['usb_devices']
        )
        
        print("└─ Сравнение накопителей...")
        storage_result = self.compare_storage_devices(
            current_config['storage_devices'], 
            self.baseline_config['storage_devices']
        )
        
        # 6. Сравнение райзеров (TRD 4.2.6.3.6)
        print("├─ Сравнение райзеров...")
        riser_result = self.compare_riser_cards(
            current_config['riser_cards'],
            self.baseline_config['riser_cards']
        )
        
        print("└─ Анализ результатов...")
        
        # Определение общего статуса
        all_statuses = [
            cpu_diff['status'], memory_diff['status'], pci_diff['status'],
            usb_diff['status'], storage_result['status'], riser_result['status']
        ]
        
        if 'FAIL' in all_statuses:
            self.overall_status = 'FAIL'
        elif 'WARNING' in all_statuses:
            self.overall_status = 'WARNING'
        else:
            self.overall_status = 'PASS'
        
        # Подсчет статистики - ИСПРАВЛЕНО: унифицированный формат differences как список
        total_differences = 0
        for result in [cpu_diff, memory_diff, pci_diff, usb_diff, storage_result, riser_result]:
            differences = result.get('differences', [])
            # Убеждаемся что differences всегда список
            if isinstance(differences, list):
                total_differences += len(differences)
            elif isinstance(differences, dict):
                # Для compatibility со старым форматом - суммируем все списки в dict
                for diff_list in differences.values():
                    if isinstance(diff_list, list):
                        total_differences += len(diff_list)
                    else:
                        # Считаем как одно различие если не список
                        total_differences += 1
            else:
                # Неожиданный формат - считаем как одно различие
                print(f"⚠️  Неожиданный формат differences в {result.get('component', 'unknown')}: {type(differences)}")
                total_differences += 1
        
        # Формируем итоговый результат
        self.diff_results = {
            'scan_info': {
                'baseline_date': self.baseline_config.get('baseline_date', 'Unknown'),
                'current_scan_date': current_config['scan_date'],
                'comparison_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            },
            'overall_status': self.overall_status,
            'component_results': {
                'processors': cpu_diff,
                'memory': memory_diff,
                'pci_devices': pci_diff,
                'usb_devices': usb_diff,
                'storage_devices': storage_result,
                'riser_cards': riser_result
            },
            'summary': {
                'total_components_checked': len([s for s in all_statuses if s != 'UNKNOWN']),
                'components_passed': sum(1 for s in all_statuses if s == 'PASS'),
                'components_warning': sum(1 for s in all_statuses if s == 'WARNING'),
                'components_failed': sum(1 for s in all_statuses if s == 'FAIL'),
                'total_differences': total_differences
            },
            'current_config': current_config
        }
        
        return self.diff_results
    
    def save_diff_report(self, output_path: str) -> None:
        """Сохранение отчета о различиях"""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with output_file.open('w', encoding='utf-8') as f:
            json.dump(self.diff_results, f, ensure_ascii=False, indent=2)
        
        print(f"📄 Отчет HW-Diff сохранен: {output_file}")

    def _escalate_status(self, current_status: str, new_status: str) -> str:
        """
        Эскалация статуса по приоритету (PASS < WARNING < FAIL < ERROR)
        
        Args:
            current_status: Текущий статус
            new_status: Новый статус для сравнения
            
        Returns:
            Статус с наибольшим приоритетом
        """
        current_priority = self.STATUS_PRIORITY.get(current_status, 0)
        new_priority = self.STATUS_PRIORITY.get(new_status, 0)
        
        if new_priority > current_priority:
            return new_status
        return current_status

if __name__ == '__main__':
    # Пример использования модуля
    print("Модуль сравнения аппаратных конфигураций")
    print("Использование:")
    print("  from hw_diff_module import HardwareDiff")
    print("  hw_diff = HardwareDiff('baseline.json')")
    print("  results = hw_diff.perform_full_diff()")
    print("  hw_diff.save_diff_report('diff_report.json')") 