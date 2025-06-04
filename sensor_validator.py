#!/usr/bin/env python3
"""
Модуль валидации сенсоров BMC согласно TRD 4.2.8.2.1-4.2.8.2.4
Использует расширенный sensor_limits.json со всеми реальными сенсорами
"""

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional
from datetime import datetime

class SensorValidator:
    """Класс для валидации всех сенсоров BMC согласно эталонным пределам"""
    
    # Полностью допустимые статусы сенсоров (нормализованные к нижнему регистру)
    ACCEPTABLE_STATUSES = {'ok', 'nc'}  # ok = OK, nc = No Contact (для пустых слотов)
    
    # Статусы, допустимые но с предупреждением (nr = No Reading - данные недоступны)
    WARNING_STATUSES = {'nr', 'ns'}  # nr = No Reading, ns = Not Specified
    
    def __init__(self, limits_file: str):
        """
        Инициализация валидатора
        
        Args:
            limits_file: Путь к JSON файлу с пределами сенсоров
        """
        self.limits_file = Path(limits_file)
        self.limits = self._load_limits()
        self.validation_results = {}
        
    def _load_limits(self) -> Dict:
        """Загрузка пределов сенсоров из JSON файла"""
        if not self.limits_file.exists():
            raise FileNotFoundError(f'Файл пределов сенсоров не найден: {self.limits_file}')
        
        with self.limits_file.open('r', encoding='utf-8') as f:
            return json.load(f)
    
    def _is_sensor_status_ok(self, status: str) -> bool:
        """
        Проверка корректности статуса сенсора (только полностью OK статусы)
        
        Args:
            status: Статус сенсора из ipmitool
            
        Returns:
            True если статус считается полностью нормальным
        """
        return status.lower() in self.ACCEPTABLE_STATUSES
    
    def _is_sensor_status_warning(self, status: str) -> bool:
        """
        Проверка статуса сенсора на предупреждение
        
        Args:
            status: Статус сенсора из ipmitool
            
        Returns:
            True если статус допустим но с предупреждением
        """
        return status.lower() in self.WARNING_STATUSES
    
    def _parse_sensor_value(self, value_str: str) -> Optional[float]:
        """
        Безопасный парсинг значения сенсора с обработкой всех специальных случаев
        
        Args:
            value_str: Строковое значение сенсора
            
        Returns:
            Числовое значение или None если значение не может быть преобразовано
        """
        # Обработка специальных строковых значений
        value_lower = value_str.lower().strip()
        
        # Значения, которые означают отсутствие данных
        if value_lower in ['na', 'disabled', 'n/a', 'not available', 'not specified', 'unknown', '']:
            return None
        
        try:
            # Обработка локализации (замена запятой на точку)
            normalized_value = value_str.replace(',', '.')
            return float(normalized_value)
        except (ValueError, TypeError):
            # Если не удалось преобразовать - возвращаем None
            return None
    
    def collect_sensor_data(self, conf: Dict[str, str]) -> Dict[str, Dict]:
        """
        Сбор данных со всех сенсоров BMC
        
        Args:
            conf: Конфигурация с параметрами BMC (bmc_ip, bmc_user, bmc_pass)
        
        Returns:
            Словарь с данными сенсоров: {sensor_name: {value, unit, status}}
        
        Raises:
            RuntimeError: При ошибке сбора данных сенсоров
        """
        print("📊 Сбор данных сенсоров BMC...")
        
        result = subprocess.run([
            'ipmitool', '-I', 'lanplus', '-H', conf['bmc_ip'],
            '-U', conf['bmc_user'], '-P', conf['bmc_pass'],
            'sensor', 'list'
        ], capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            raise RuntimeError(f'Ошибка получения данных сенсоров: {result.stderr}')
        
        sensors = {}
        for line in result.stdout.splitlines():
            if '|' in line:
                parts = line.split('|')
                if len(parts) >= 4:
                    name = parts[0].strip()
                    value = parts[1].strip()
                    unit = parts[2].strip()
                    status = parts[3].strip()
                    
                    sensors[name] = {
                        'value': value,
                        'unit': unit,
                        'status': status,
                        'raw_line': line.strip()
                    }
        
        print(f"✅ Собрано {len(sensors)} сенсоров")
        return sensors
    
    def _create_validation_result_dict(self) -> Dict:
        """Фабрика для создания стандартной структуры результатов валидации"""
        return {
            'actually_checked': 0,  # Реально обработанные сенсоры
            'passed_sensors': 0,
            'warning_sensors': 0,
            'failed_sensors': 0,
            'missing_sensors': 0,
            'skipped_sensors': 0,  # Пропущенные опциональные
            'violations': [],
            'status': 'PASS'
        }
    
    def validate_voltage_sensors(self, sensors: Dict[str, Dict]) -> Dict:
        """Валидация напряжений согласно voltage_limits"""
        voltage_results = self._create_validation_result_dict()
        
        voltage_limits = self.limits.get('voltage_limits', {})
        
        for sensor_name, limits in voltage_limits.items():
            if sensor_name == 'comment':
                continue
            
            if sensor_name not in sensors:
                voltage_results['missing_sensors'] += 1
                voltage_results['violations'].append({
                    'sensor': sensor_name,
                    'type': 'MISSING',
                    'message': f'Сенсор {sensor_name} отсутствует в системе'
                })
                continue
            
            voltage_results['actually_checked'] += 1
            sensor_data = sensors[sensor_name]
            
            # Проверка доступности сенсора
            if sensor_data['value'] == 'na':
                voltage_results['violations'].append({
                    'sensor': sensor_name,
                    'type': 'UNAVAILABLE',
                    'message': f'Сенсор {sensor_name} недоступен: value=na'
                })
                continue
            
            # Проверка статуса сенсора
            if not self._is_sensor_status_ok(sensor_data['status']):
                if self._is_sensor_status_warning(sensor_data['status']):
                    voltage_results['warning_sensors'] += 1
                    voltage_results['violations'].append({
                        'sensor': sensor_name,
                        'type': 'STATUS_WARNING',
                        'message': f'Сенсор {sensor_name} имеет статус предупреждения: {sensor_data["status"]}'
                    })
                else:
                    voltage_results['violations'].append({
                        'sensor': sensor_name,
                        'type': 'STATUS_ERROR',
                        'message': f'Сенсор {sensor_name} имеет неправильный статус: {sensor_data["status"]}'
                    })
                continue
            
            # Парсинг значения напряжения
            voltage = self._parse_sensor_value(sensor_data['value'])
            if voltage is None:
                voltage_results['violations'].append({
                    'sensor': sensor_name,
                    'type': 'PARSE_ERROR',
                    'message': f'Ошибка парсинга значения {sensor_name}: {sensor_data["value"]}'
                })
                continue
            
            # Проверка пределов
            min_limit = limits.get('min')
            max_limit = limits.get('max')
            warn_min_limit = limits.get('warn_min')
            warn_max_limit = limits.get('warn_max')
            
            if min_limit is not None and voltage < min_limit:
                voltage_results['failed_sensors'] += 1
                voltage_results['violations'].append({
                    'sensor': sensor_name,
                    'type': 'UNDERVOLTAGE',
                    'value': voltage,
                    'limit': min_limit,
                    'message': f'{sensor_name}: {voltage}V < {min_limit}V (FAIL)'
                })
            elif max_limit is not None and voltage > max_limit:
                voltage_results['failed_sensors'] += 1
                voltage_results['violations'].append({
                    'sensor': sensor_name,
                    'type': 'OVERVOLTAGE',
                    'value': voltage,
                    'limit': max_limit,
                    'message': f'{sensor_name}: {voltage}V > {max_limit}V (FAIL)'
                })
            elif warn_min_limit is not None and voltage < warn_min_limit:
                voltage_results['warning_sensors'] += 1
                voltage_results['violations'].append({
                    'sensor': sensor_name,
                    'type': 'VOLTAGE_WARNING_LOW',
                    'value': voltage,
                    'limit': warn_min_limit,
                    'message': f'{sensor_name}: {voltage}V < {warn_min_limit}V (предупреждение)'
                })
            elif warn_max_limit is not None and voltage > warn_max_limit:
                voltage_results['warning_sensors'] += 1
                voltage_results['violations'].append({
                    'sensor': sensor_name,
                    'type': 'VOLTAGE_WARNING_HIGH',
                    'value': voltage,
                    'limit': warn_max_limit,
                    'message': f'{sensor_name}: {voltage}V > {warn_max_limit}V (предупреждение)'
                })
            else:
                voltage_results['passed_sensors'] += 1
        
        # Определение общего статуса
        if voltage_results['failed_sensors'] > 0:
            voltage_results['status'] = 'FAIL'
        elif voltage_results['missing_sensors'] > 0 or voltage_results['warning_sensors'] > 0:
            voltage_results['status'] = 'WARNING'
        
        return voltage_results
    
    def validate_temperature_sensors(self, sensors: Dict[str, Dict]) -> Dict:
        """Валидация температур согласно temperature_limits"""
        temp_results = self._create_validation_result_dict()
        
        temp_limits = self.limits.get('temperature_limits', {})
        
        for sensor_name, limits in temp_limits.items():
            if sensor_name == 'comment':
                continue
            
            if sensor_name not in sensors:
                # Проверяем, является ли сенсор критическим или опциональным
                critical_sensors = self.limits.get('validation_rules', {}).get('critical_sensors', [])
                optional_sensors = self.limits.get('validation_rules', {}).get('optional_sensors', [])
                
                if sensor_name in critical_sensors:
                    temp_results['missing_sensors'] += 1
                    temp_results['violations'].append({
                        'sensor': sensor_name,
                        'type': 'MISSING_CRITICAL',
                        'message': f'Критический сенсор {sensor_name} отсутствует'
                    })
                elif sensor_name in optional_sensors:
                    # Опциональный сенсор отсутствует - это нормально, пропускаем
                    temp_results['skipped_sensors'] += 1
                    continue
                else:
                    # Неопределенный сенсор - считаем как warning
                    temp_results['missing_sensors'] += 1
                    temp_results['violations'].append({
                        'sensor': sensor_name,
                        'type': 'MISSING',
                        'message': f'Сенсор {sensor_name} отсутствует в системе'
                    })
                continue
            
            temp_results['actually_checked'] += 1
            sensor_data = sensors[sensor_name]
            
            if sensor_data['value'] == 'na':
                # Для температурных сенсоров 'na' означает отсутствие данных
                # Проверяем статус для определения причины
                status = sensor_data['status'].lower()
                
                # Правильные статусы для пустых слотов согласно TRD
                if status in ['nc', 'nr']:  # nc = No Contact, nr = No Reading
                    # Это нормально для пустых слотов - пропускаем
                    temp_results['skipped_sensors'] += 1
                    continue
                elif status == 'ok':
                    # Если статус OK, но value=na - это может быть неисправность датчика
                    temp_results['warning_sensors'] += 1
                    temp_results['violations'].append({
                        'sensor': sensor_name,
                        'type': 'SENSOR_DATA_INCONSISTENT',
                        'message': f'Сенсор {sensor_name}: статус OK, но значение недоступно (возможна неисправность датчика)'
                    })
                    continue
                else:
                    # Для других статусов с na - проверяем критичность
                    optional_sensors = self.limits.get('validation_rules', {}).get('optional_sensors', [])
                    if sensor_name in optional_sensors:
                        temp_results['skipped_sensors'] += 1
                        continue
                    else:
                        temp_results['violations'].append({
                            'sensor': sensor_name,
                            'type': 'UNAVAILABLE',
                            'message': f'Сенсор {sensor_name} недоступен: value=na, status={sensor_data["status"]}'
                        })
                        continue
            
            # Проверка статуса сенсора
            if not self._is_sensor_status_ok(sensor_data['status']):
                if self._is_sensor_status_warning(sensor_data['status']):
                    temp_results['warning_sensors'] += 1
                    temp_results['violations'].append({
                        'sensor': sensor_name,
                        'type': 'STATUS_WARNING',
                        'message': f'Сенсор {sensor_name} имеет статус предупреждения: {sensor_data["status"]}'
                    })
                else:
                    temp_results['violations'].append({
                        'sensor': sensor_name,
                        'type': 'STATUS_ERROR',
                        'message': f'Сенсор {sensor_name} имеет неправильный статус: {sensor_data["status"]}'
                    })
                continue
            
            # Парсинг температуры
            temperature = self._parse_sensor_value(sensor_data['value'])
            if temperature is None:
                temp_results['violations'].append({
                    'sensor': sensor_name,
                    'type': 'PARSE_ERROR',
                    'message': f'Ошибка парсинга температуры {sensor_name}: {sensor_data["value"]}'
                })
                continue
            
            # Проверка пределов температуры
            min_limit = limits.get('min')
            max_limit = limits.get('max')
            warn_limit = limits.get('warn')
            
            if min_limit is not None and temperature < min_limit:
                temp_results['failed_sensors'] += 1
                temp_results['violations'].append({
                    'sensor': sensor_name,
                    'type': 'UNDERTEMPERATURE',
                    'value': temperature,
                    'limit': min_limit,
                    'message': f'{sensor_name}: {temperature}°C < {min_limit}°C (возможен обрыв датчика)'
                })
            elif max_limit is not None and temperature > max_limit:
                temp_results['failed_sensors'] += 1
                temp_results['violations'].append({
                    'sensor': sensor_name,
                    'type': 'OVERTEMPERATURE',
                    'value': temperature,
                    'limit': max_limit,
                    'message': f'{sensor_name}: {temperature}°C > {max_limit}°C (КРИТИЧЕСКИЙ ПЕРЕГРЕВ!)'
                })
            elif warn_limit is not None and temperature > warn_limit:
                temp_results['warning_sensors'] += 1
                temp_results['violations'].append({
                    'sensor': sensor_name,
                    'type': 'WARNING_TEMPERATURE',
                    'value': temperature,
                    'limit': warn_limit,
                    'message': f'{sensor_name}: {temperature}°C > {warn_limit}°C (предупреждение)'
                })
            else:
                temp_results['passed_sensors'] += 1
        
        # Определение общего статуса
        if temp_results['failed_sensors'] > 0:
            temp_results['status'] = 'FAIL'
        elif temp_results['warning_sensors'] > 0 or temp_results['missing_sensors'] > 0:
            temp_results['status'] = 'WARNING'
        
        return temp_results
    
    def validate_fan_sensors(self, sensors: Dict[str, Dict]) -> Dict:
        """Валидация сенсоров вентиляторов"""
        results = self._create_validation_result_dict()
        
        for sensor_name, sensor_data in sensors.items():
            if sensor_data['unit'].lower() == 'rpm':
                results['actually_checked'] += 1
                
                # Парсим значение RPM
                rpm_value = self._parse_sensor_value(sensor_data['value'])
                if rpm_value is None:
                    results['skipped_sensors'] += 1
                    continue
                
                # Проверяем статус сенсора
                if not self._is_sensor_status_ok(sensor_data['status']):
                    if self._is_sensor_status_warning(sensor_data['status']):
                        results['warning_sensors'] += 1
                    else:
                        results['failed_sensors'] += 1
                        results['violations'].append({
                            'sensor': sensor_name,
                            'value': rpm_value,
                            'status': sensor_data['status'],
                            'issue': f'Неправильный статус вентилятора: {sensor_data["status"]}'
                        })
                        continue
                
                # Проверяем разумные пределы RPM (минимум 100 RPM, максимум 20000 RPM)
                if rpm_value < 100 or rpm_value > 20000:
                    results['failed_sensors'] += 1
                    results['violations'].append({
                        'sensor': sensor_name,
                        'value': rpm_value,
                        'issue': f'Скорость вентилятора вне разумных пределов: {rpm_value} RPM'
                    })
                else:
                    results['passed_sensors'] += 1
        
        # Определяем статус категории
        if results['failed_sensors'] > 0:
            results['status'] = 'FAIL'
        elif results['warning_sensors'] > 0:
            results['status'] = 'WARNING'
        else:
            results['status'] = 'PASS'
        
        return results
    
    def validate_power_sensors(self, sensors: Dict[str, Dict]) -> Dict:
        """Валидация мощности согласно power_limits"""
        results = self._create_validation_result_dict()
        
        for sensor_name, sensor_data in sensors.items():
            if sensor_data['unit'].lower() == 'watts':
                results['actually_checked'] += 1
                
                # Парсим значение мощности
                power_value = self._parse_sensor_value(sensor_data['value'])
                if power_value is None:
                    results['skipped_sensors'] += 1
                    continue
                
                # Проверяем статус сенсора
                if not self._is_sensor_status_ok(sensor_data['status']):
                    if self._is_sensor_status_warning(sensor_data['status']):
                        results['warning_sensors'] += 1
                    else:
                        results['failed_sensors'] += 1
                        results['violations'].append({
                            'sensor': sensor_name,
                            'value': power_value,
                            'status': sensor_data['status'],
                            'issue': f'Неправильный статус сенсора мощности: {sensor_data["status"]}'
                        })
                        continue
                
                # Проверяем разумные пределы мощности (0-2000W для серверов)
                if power_value < 0 or power_value > 2000:
                    results['failed_sensors'] += 1
                    results['violations'].append({
                        'sensor': sensor_name,
                        'value': power_value,
                        'issue': f'Потребление мощности вне разумных пределов: {power_value}W'
                    })
                else:
                    results['passed_sensors'] += 1
        
        # Определяем статус категории
        if results['failed_sensors'] > 0:
            results['status'] = 'FAIL'
        elif results['warning_sensors'] > 0:
            results['status'] = 'WARNING'
        else:
            results['status'] = 'PASS'
        
        return results
    
    def validate_discrete_sensors(self, sensors: Dict[str, Dict]) -> Dict:
        """Валидация дискретных сенсоров согласно discrete_sensors"""
        discrete_results = self._create_validation_result_dict()
        
        discrete_config = self.limits.get('discrete_sensors', {})
        acceptable_statuses = discrete_config.get('acceptable_statuses', {})
        critical_sensors = discrete_config.get('critical_if_different', [])
        
        for sensor_name, expected_statuses in acceptable_statuses.items():
            if sensor_name not in sensors:
                discrete_results['violations'].append({
                    'sensor': sensor_name,
                    'type': 'MISSING',
                    'message': f'Дискретный сенсор {sensor_name} отсутствует'
                })
                continue
            
            discrete_results['actually_checked'] += 1
            sensor_data = sensors[sensor_name]
            actual_status = sensor_data['status']
            
            if actual_status in expected_statuses:
                discrete_results['passed_sensors'] += 1
            else:
                # Определяем критичность
                if sensor_name in critical_sensors:
                    discrete_results['failed_sensors'] += 1
                    discrete_results['violations'].append({
                        'sensor': sensor_name,
                        'type': 'CRITICAL_STATUS',
                        'value': actual_status,
                        'expected': expected_statuses,
                        'message': f'{sensor_name}: критический статус {actual_status}, ожидался {expected_statuses}'
                    })
                else:
                    discrete_results['warning_sensors'] += 1
                    discrete_results['violations'].append({
                        'sensor': sensor_name,
                        'type': 'WARNING_STATUS',
                        'value': actual_status,
                        'expected': expected_statuses,
                        'message': f'{sensor_name}: неожиданный статус {actual_status}, ожидался {expected_statuses}'
                    })
        
        # Определение общего статуса
        if discrete_results['failed_sensors'] > 0:
            discrete_results['status'] = 'FAIL'
        elif discrete_results['warning_sensors'] > 0:
            discrete_results['status'] = 'WARNING'
        
        return discrete_results
    
    def perform_full_validation(self, conf: Dict[str, str]) -> Dict:
        """Выполнение полной валидации всех сенсоров с обработкой ошибок"""
        print("🔍 Запуск полной валидации сенсоров BMC...")
        
        validation_results = {
            'validation_info': {
                'limits_file': str(self.limits_file),
                'validation_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'board_model': self.limits.get('board_model', 'Unknown'),
                'total_sensors_available': 0
            },
            'overall_status': 'ERROR',
            'summary': {
                'total_checked': 0,
                'total_passed': 0,
                'total_violations': 0,
                'categories_checked': 0
            },
            'category_results': {},
            'raw_sensor_data': {},
            'error_details': None
        }
        
        try:
            # Сбор данных сенсоров с обработкой ошибок
            try:
                sensors = self.collect_sensor_data(conf)
                validation_results['validation_info']['total_sensors_available'] = len(sensors)
                validation_results['raw_sensor_data'] = sensors
            except Exception as sensor_error:
                # Критическая ошибка сбора данных - возвращаем ERROR статус
                validation_results['error_details'] = {
                    'error_type': 'SENSOR_COLLECTION_FAILED',
                    'error_message': str(sensor_error),
                    'stage': 'collect_sensor_data'
                }
                print(f"❌ Критическая ошибка сбора данных сенсоров: {sensor_error}")
                return validation_results
            
            # Валидация по категориям с обработкой ошибок каждой категории
            category_functions = [
                ('voltages', self.validate_voltage_sensors, "Валидация напряжений"),
                ('temperatures', self.validate_temperature_sensors, "Валидация температур"),
                ('fans', self.validate_fan_sensors, "Валидация вентиляторов"),
                ('power', self.validate_power_sensors, "Валидация мощности"),
                ('discrete', self.validate_discrete_sensors, "Валидация дискретных сенсоров")
            ]
            
            category_results = {}
            successful_categories = 0
            
            for category_name, validation_func, description in category_functions:
                try:
                    print(f"├─ {description}...")
                    category_result = validation_func(sensors)
                    category_results[category_name] = category_result
                    successful_categories += 1
                except Exception as category_error:
                    print(f"❌ Ошибка в {description}: {category_error}")
                    category_results[category_name] = {
                        'status': 'ERROR',
                        'error': str(category_error),
                        'actually_checked': 0,
                        'passed_sensors': 0,
                        'warning_sensors': 0,
                        'failed_sensors': 0,
                        'missing_sensors': 0,
                        'skipped_sensors': 0,
                        'violations': []
                    }
            
            validation_results['category_results'] = category_results
            validation_results['summary']['categories_checked'] = successful_categories
            
            # Определение общего статуса на основе результатов категорий
            all_statuses = [result['status'] for result in category_results.values()]
            
            if 'ERROR' in all_statuses:
                validation_results['overall_status'] = 'ERROR'
            elif 'FAIL' in all_statuses:
                validation_results['overall_status'] = 'FAIL'
            elif 'WARNING' in all_statuses:
                validation_results['overall_status'] = 'WARNING'
            else:
                validation_results['overall_status'] = 'PASS'
            
            # Исправленная общая статистика - считаем только реально обработанные сенсоры
            total_violations = sum([
                len(result['violations']) for result in category_results.values()
            ])
            
            total_checked = sum([
                result.get('actually_checked', 0) for result in category_results.values()
            ])
            
            total_passed = sum([
                result.get('passed_sensors', 0) for result in category_results.values()
            ])
            
            validation_results['summary'].update({
                'total_checked': total_checked,
                'total_passed': total_passed,
                'total_violations': total_violations
            })
            
            print(f"✅ Валидация завершена: {validation_results['overall_status']}")
            print(f"📊 Обработано {total_checked} сенсоров, {total_passed} прошли проверку")
            
        except Exception as unexpected_error:
            # Непредвиденная ошибка в валидации
            validation_results['error_details'] = {
                'error_type': 'UNEXPECTED_ERROR',
                'error_message': str(unexpected_error),
                'stage': 'perform_full_validation'
            }
            print(f"❌ Непредвиденная ошибка валидации: {unexpected_error}")
        
        return validation_results
    
    def save_validation_report(self, results: Dict, output_path: str) -> None:
        """Сохранение отчета валидации"""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with output_file.open('w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        print(f"📄 Отчет валидации сенсоров сохранен: {output_file}")

    def check_temp_thresholds(self, sensor_name: str, value: float, limits: Dict) -> Tuple[str, str]:
        """Проверяет температурные пороги для сенсора"""
        if value < limits.get('critical_low', -40):
            return 'FAIL', f'{sensor_name}: критически низкая температура {value}°C'
        elif value > limits.get('critical_high', 100):
            return 'FAIL', f'{sensor_name}: критически высокая температура {value}°C'
        elif value < limits.get('warning_low', 0):
            return 'WARNING', f'{sensor_name}: низкая температура {value}°C'
        elif value > limits.get('warning_high', 80):
            return 'WARNING', f'{sensor_name}: высокая температура {value}°C'
        else:
            return 'PASS', ''

def main():
    """Основная функция для демонстрации"""
    import sys
    
    if len(sys.argv) > 1:
        limits_file = sys.argv[1]
    else:
        limits_file = 'reference/sensor_limits.json'
    
    # Пример конфигурации BMC
    if len(sys.argv) > 4:
        conf = {
            'bmc_ip': sys.argv[2],
            'bmc_user': sys.argv[3],
            'bmc_pass': sys.argv[4]
        }
    else:
        print("Использование: python3 sensor_validator.py <limits_file> <bmc_ip> <bmc_user> <bmc_pass>")
        sys.exit(1)
    
    try:
        # Создаем валидатор
        validator = SensorValidator(limits_file)
        
        # Выполняем валидацию
        results = validator.perform_full_validation(conf)
        
        # Выводим результаты
        print(f"\n🎯 РЕЗУЛЬТАТ ВАЛИДАЦИИ: {results['overall_status']}")
        print(f"📊 Сенсоры: {results['summary']['total_checked']} проверено, {results['summary']['total_passed']} прошли")
        print(f"⚠️  Всего нарушений: {results['summary']['total_violations']}")
        
        # Сохраняем отчет
        validator.save_validation_report(results, 'logs/sensor_validation_report.json')
        
        # Возвращаем код выхода
        if results['overall_status'] == 'FAIL':
            sys.exit(1)
        elif results['overall_status'] == 'WARNING':
            sys.exit(2)
        else:
            sys.exit(0)
            
    except Exception as e:
        print(f"❌ Ошибка валидации сенсоров: {e}")
        sys.exit(3)

if __name__ == '__main__':
    main() 