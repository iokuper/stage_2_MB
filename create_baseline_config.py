#!/usr/bin/env python3
"""
Скрипт для создания эталонной (baseline) конфигурации системы RSMB-MS93-FS0
"""

import subprocess
import json
import re
from datetime import datetime

def get_cpu_info():
    """Получение детальной информации о процессорах"""
    result = subprocess.run(['dmidecode', '-t', '4'], capture_output=True, text=True)
    cpus = []
    current_cpu = {}
    
    for line in result.stdout.splitlines():
        line = line.strip()
        if 'Socket Designation:' in line:
            if current_cpu:
                cpus.append(current_cpu)
            current_cpu = {'socket': line.split(':', 1)[1].strip()}
        elif 'Version:' in line:
            current_cpu['model'] = line.split(':', 1)[1].strip()
        elif 'Core Count:' in line:
            try:
                current_cpu['cores'] = int(line.split(':', 1)[1].strip())
            except ValueError:
                current_cpu['cores'] = 0
        elif 'Thread Count:' in line:
            try:
                current_cpu['threads'] = int(line.split(':', 1)[1].strip())
            except ValueError:
                current_cpu['threads'] = 0
        elif 'Current Speed:' in line:
            current_cpu['speed_mhz'] = line.split(':', 1)[1].strip()
    
    if current_cpu:
        cpus.append(current_cpu)
    
    return cpus

def get_memory_info():
    """Получение информации о модулях памяти"""
    result = subprocess.run(['dmidecode', '-t', '17'], capture_output=True, text=True)
    dimms = []
    current_dimm = {}
    
    for line in result.stdout.splitlines():
        line = line.strip()
        if 'Locator:' in line and 'Bank Locator' not in line:
            if current_dimm and 'slot' in current_dimm:  # Добавляем только если слот определен
                dimms.append(current_dimm)
            current_dimm = {'slot': line.split(':', 1)[1].strip()}
        elif 'Size:' in line:
            size = line.split(':', 1)[1].strip()
            current_dimm['size'] = size
            current_dimm['populated'] = 'GB' in size
        elif 'Manufacturer:' in line:
            current_dimm['manufacturer'] = line.split(':', 1)[1].strip()
        elif 'Part Number:' in line:
            current_dimm['part_number'] = line.split(':', 1)[1].strip()
        elif 'Speed:' in line and 'MT/s' in line:
            current_dimm['speed'] = line.split(':', 1)[1].strip()
        elif 'Configured Memory Speed:' in line:
            current_dimm['configured_speed'] = line.split(':', 1)[1].strip()
    
    # Добавляем последний элемент только если слот определен
    if current_dimm and 'slot' in current_dimm:
        dimms.append(current_dimm)
    
    return dimms

def get_pci_info():
    """Получение информации о PCIe устройствах"""
    result = subprocess.run(['lspci', '-v'], capture_output=True, text=True)
    pci_devices = []
    
    for line in result.stdout.splitlines():
        if re.match(r'^[0-9a-f]{2}:[0-9a-f]{2}\.[0-9]', line):
            parts = line.split(': ', 1)
            if len(parts) == 2:
                bdf = parts[0]
                description = parts[1]
                device_class = description.split(':')[0] if ':' in description else description
                
                # Получаем дополнительную информацию о PCIe ширине и скорости
                pci_detail = subprocess.run(['lspci', '-vv', '-s', bdf], 
                                          capture_output=True, text=True)
                
                width = 'unknown'
                speed = 'unknown'
                for detail_line in pci_detail.stdout.splitlines():
                    if 'LnkCap:' in detail_line:
                        # Парсим ширину и скорость линков
                        if 'Width x' in detail_line:
                            width_match = re.search(r'Width x(\d+)', detail_line)
                            if width_match:
                                width = f"x{width_match.group(1)}"
                        if 'Speed' in detail_line:
                            speed_match = re.search(r'Speed (\d+(?:\.\d+)?GT/s)', detail_line)
                            if speed_match:
                                speed = speed_match.group(1)
                        break
                
                pci_devices.append({
                    'bdf': bdf,
                    'description': description,
                    'class': device_class,
                    'width': width,
                    'speed': speed
                })
    
    return pci_devices

def get_usb_info():
    """Получение информации о USB устройствах"""
    result = subprocess.run(['lsusb'], capture_output=True, text=True)
    usb_devices = []
    
    for line in result.stdout.splitlines():
        if 'Bus' in line and 'Device' in line:
            # Парсим: Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
            parts = line.split()
            if len(parts) >= 6:
                bus = parts[1]
                device = parts[3].rstrip(':')
                vid_pid = parts[5]
                description = ' '.join(parts[6:])
                
                # Определяем версию USB
                usb_version = 'USB2.0'
                if 'root hub' in description.lower():
                    if '3.0' in description or bus == '002':  # Обычно bus 002 = USB3
                        usb_version = 'USB3.0'
                
                usb_devices.append({
                    'bus': bus,
                    'device': device,
                    'vid_pid': vid_pid,
                    'description': description,
                    'usb_version': usb_version
                })
    
    return usb_devices

def get_storage_info():
    """Получение информации о накопителях"""
    result = subprocess.run(['lsblk', '-J', '-o', 'NAME,MODEL,SIZE,TYPE'], 
                          capture_output=True, text=True)
    storage_devices = []
    
    try:
        lsblk_data = json.loads(result.stdout)
        for device in lsblk_data.get('blockdevices', []):
            if device.get('type') == 'disk':
                storage_devices.append({
                    'name': device.get('name', ''),
                    'model': device.get('model', ''),
                    'size': device.get('size', ''),
                    'type': 'nvme' if device.get('name', '').startswith('nvme') else 'sata',
                    'interface': 'NVMe' if device.get('name', '').startswith('nvme') else 'SATA'
                })
    except json.JSONDecodeError:
        # Fallback если JSON парсинг не удался
        result = subprocess.run(['lsblk', '-o', 'NAME,MODEL,SIZE,TYPE'], 
                              capture_output=True, text=True)
        for line in result.stdout.splitlines()[1:]:  # Пропускаем заголовок
            parts = line.split()
            if len(parts) >= 4 and parts[3] == 'disk':
                name = parts[0]
                model = parts[1] if len(parts) > 1 else 'Unknown'
                size = parts[2] if len(parts) > 2 else 'Unknown'
                storage_devices.append({
                    'name': name,
                    'model': model,
                    'size': size,
                    'type': 'nvme' if name.startswith('nvme') else 'sata',
                    'interface': 'NVMe' if name.startswith('nvme') else 'SATA'
                })
    
    return storage_devices

def get_riser_info():
    """Получение информации о райзерах через FRU (требует конфигурации BMC)"""
    print("📋 Сканирование FRU для поиска райзеров...")
    
    # Попытка найти конфигурацию BMC
    import os
    config_file = 'agent.conf'
    
    if not os.path.exists(config_file):
        print(f"⚠️  Файл конфигурации BMC не найден: {config_file}")
        print("   Создание эталонных данных райзеров на основе спецификации...")
        return [
            {
                'slot': 'RISER_SLOT_1',
                'fru_product_name': 'RSMB-MS93-FS0-RISER-1',
                'fru_manufacturer': 'GIGA-BYTE TECHNOLOGY CO., LTD',
                'fru_part_number': '25VH1-1A00-11NN',
                'fru_serial_number': 'Required',
                'populated': True,
                'pcie_slots': [
                    {'slot_id': 'SLOT_1', 'speed': 'PCIe 4.0', 'width': 'x16', 'status': 'active'},
                    {'slot_id': 'SLOT_2', 'speed': 'PCIe 4.0', 'width': 'x16', 'status': 'active'}
                ]
            },
            {
                'slot': 'RISER_SLOT_2',
                'fru_product_name': 'RSMB-MS93-FS0-RISER-2',
                'fru_manufacturer': 'GIGA-BYTE TECHNOLOGY CO., LTD',
                'fru_part_number': '25VH1-1A00-22NN',
                'fru_serial_number': 'Required',
                'populated': True,
                'pcie_slots': [
                    {'slot_id': 'SLOT_3', 'speed': 'PCIe 4.0', 'width': 'x16', 'status': 'active'}
                ]
            },
            {
                'slot': 'RISER_SLOT_3',
                'fru_product_name': 'RSMB-MS93-FS0-RISER-3',
                'fru_manufacturer': 'GIGA-BYTE TECHNOLOGY CO., LTD',
                'fru_part_number': '25VH1-1A00-33NN',
                'fru_serial_number': 'Required',
                'populated': False,
                'pcie_slots': []
            }
        ]
    
    try:
        with open(config_file, 'r') as f:
            conf = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"⚠️  Ошибка чтения конфигурации BMC: {e}")
        return []
    
    risers = []
    
    # Сканируем FRU записи в поисках райзеров
    for fru_id in range(1, 10):  # FRU ID 1-9 для периферийных устройств
        try:
            result = subprocess.run([
                'ipmitool', '-I', 'lanplus', '-H', conf['bmc_ip'],
                '-U', conf['bmc_user'], '-P', conf['bmc_pass'],
                'fru', 'print', str(fru_id)
            ], capture_output=True, text=True, timeout=15)
            
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
                if ('RISER' in product_name or 
                    'RSMB-MS93' in product_name or
                    'RISER' in fru_data.get('fru_part_number', '').upper()):
                    
                    # Определяем слот райзера
                    if 'RISER-1' in product_name or '1A00-11' in fru_data.get('fru_part_number', ''):
                        slot = 'RISER_SLOT_1'
                        pcie_slots = [
                            {'slot_id': 'SLOT_1', 'speed': 'PCIe 4.0', 'width': 'x16', 'status': 'active'},
                            {'slot_id': 'SLOT_2', 'speed': 'PCIe 4.0', 'width': 'x16', 'status': 'active'}
                        ]
                    elif 'RISER-2' in product_name or '1A00-22' in fru_data.get('fru_part_number', ''):
                        slot = 'RISER_SLOT_2'
                        pcie_slots = [
                            {'slot_id': 'SLOT_3', 'speed': 'PCIe 4.0', 'width': 'x16', 'status': 'active'}
                        ]
                    elif 'RISER-3' in product_name or '1A00-33' in fru_data.get('fru_part_number', ''):
                        slot = 'RISER_SLOT_3'
                        pcie_slots = []
                    else:
                        slot = f'RISER_SLOT_{fru_id}'
                        pcie_slots = []
                    
                    riser_info = {
                        'slot': slot,
                        'populated': True,
                        'pcie_slots': pcie_slots,
                        **fru_data
                    }
                    
                    risers.append(riser_info)
                    print(f"   ✅ Найден райзер: {slot} ({fru_data.get('fru_product_name', 'Unknown')})")
                    
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
        except Exception as e:
            print(f"   ⚠️  Ошибка при сканировании FRU {fru_id}: {e}")
            continue
    
    if not risers:
        print("   ⚠️  Райзеры не найдены через FRU - используются данные спецификации")
        # Возвращаем эталонные данные из спецификации
        return [
            {
                'slot': 'RISER_SLOT_1',
                'fru_product_name': 'RSMB-MS93-FS0-RISER-1',
                'fru_manufacturer': 'GIGA-BYTE TECHNOLOGY CO., LTD',
                'fru_part_number': '25VH1-1A00-11NN',
                'fru_serial_number': 'Required',
                'populated': True,
                'pcie_slots': [
                    {'slot_id': 'SLOT_1', 'speed': 'PCIe 4.0', 'width': 'x16', 'status': 'active'},
                    {'slot_id': 'SLOT_2', 'speed': 'PCIe 4.0', 'width': 'x16', 'status': 'active'}
                ]
            },
            {
                'slot': 'RISER_SLOT_2',
                'fru_product_name': 'RSMB-MS93-FS0-RISER-2',
                'fru_manufacturer': 'GIGA-BYTE TECHNOLOGY CO., LTD',
                'fru_part_number': '25VH1-1A00-22NN',
                'fru_serial_number': 'Required',
                'populated': True,
                'pcie_slots': [
                    {'slot_id': 'SLOT_3', 'speed': 'PCIe 4.0', 'width': 'x16', 'status': 'active'}
                ]
            }
        ]
    
    return risers

def main():
    print("=== СОЗДАНИЕ ЭТАЛОННОЙ КОНФИГУРАЦИИ RSMB-MS93-FS0 ===")
    
    # Собираем компоненты
    print("📊 Сбор информации о компонентах...")
    
    cpus = get_cpu_info()
    memory = get_memory_info()
    pci_devices = get_pci_info()
    usb_devices = get_usb_info()
    storage = get_storage_info()
    risers = get_riser_info()
    
    # Создаем эталонную конфигурацию
    baseline_config = {
        'board_model': 'RSMB-MS93-FS0',
        'baseline_date': datetime.now().strftime('%Y-%m-%d'),
        'baseline_version': '1.0',
        'description': 'Эталонная конфигурация материнской платы RSMB-MS93-FS0 (золотой образец)',
        
        'processors': cpus,
        'memory_modules': memory,
        'pci_devices': pci_devices,
        'usb_devices': usb_devices,
        'storage_devices': storage,
        'riser_cards': risers,
        
        'expected_counts': {
            'cpu_sockets': len(cpus),
            'cpu_cores_total': sum(cpu.get('cores', 0) for cpu in cpus),
            'cpu_threads_total': sum(cpu.get('threads', 0) for cpu in cpus),
            'memory_slots_total': len(memory),
            'memory_slots_populated': len([d for d in memory if d.get('populated', False)]),
            'memory_size_total_gb': sum(
                int(d['size'].split()[0]) for d in memory 
                if d.get('populated', False) and 'GB' in d.get('size', '')
            ),
            'pci_devices': len(pci_devices),
            'usb_devices': len(usb_devices),
            'nvme_drives': len([d for d in storage if d['type'] == 'nvme']),
            'sata_drives': len([d for d in storage if d['type'] == 'sata']),
            'storage_devices_total': len(storage),
            'riser_slots_total': len(risers),
            'riser_slots_populated': len([r for r in risers if r.get('populated', False)])
        },
        
        'validation_rules': {
            'cpu_tolerance': 'exact',  # CPU должны точно совпадать
            'memory_tolerance': 'slots_and_size',  # Слоты и размер памяти
            'pci_tolerance': 'critical_devices',  # Критические устройства должны присутствовать
            'usb_tolerance': 'hubs_and_controllers',  # USB хабы и контроллеры
            'storage_tolerance': 'type_and_count',  # Тип и количество накопителей
            'riser_tolerance': 'critical_only'  # Только критические райзеры должны присутствовать
        }
    }
    
    # Выводим сводку
    print("\n📋 Сводка эталонной конфигурации:")
    print(f"├─ Процессоры: {baseline_config['expected_counts']['cpu_sockets']} сокетов")
    print(f"│  ├─ Всего ядер: {baseline_config['expected_counts']['cpu_cores_total']}")
    print(f"│  └─ Всего потоков: {baseline_config['expected_counts']['cpu_threads_total']}")
    print(f"├─ Память: {baseline_config['expected_counts']['memory_slots_populated']}/{baseline_config['expected_counts']['memory_slots_total']} слотов ({baseline_config['expected_counts']['memory_size_total_gb']} GB)")
    print(f"├─ PCI устройства: {baseline_config['expected_counts']['pci_devices']}")
    print(f"├─ USB устройства: {baseline_config['expected_counts']['usb_devices']}")
    print(f"├─ Накопители: {baseline_config['expected_counts']['nvme_drives']} NVMe + {baseline_config['expected_counts']['sata_drives']} SATA")
    print(f"└─ Райзеры: {baseline_config['expected_counts']['riser_slots_populated']}/{baseline_config['expected_counts']['riser_slots_total']} установлено")
    
    # Сохраняем в файл
    with open('reference/inventory_RSMB-MS93.json', 'w', encoding='utf-8') as f:
        json.dump(baseline_config, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Эталонная конфигурация сохранена: reference/inventory_RSMB-MS93.json")
    print(f"📅 Дата создания: {baseline_config['baseline_date']}")
    
    return baseline_config

if __name__ == '__main__':
    main() 