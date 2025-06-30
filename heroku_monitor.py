#!/usr/bin/env python3
"""
Särtshöga Vingård Availability Monitor - Heroku Version
Monitors Sirvoy booking system for room availability using direct widget access
"""

import requests
import json
import html
import os
import re
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time
import logging
from bs4 import BeautifulSoup

# Configure logging for Heroku
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class HerokuSartshogaMonitor:
    def __init__(self):
        self.base_url = "https://www.sartshogavingard.se/bo-ata"
        self.sirvoy_api_base = "https://secured.sirvoy.com/engine/"
        
        # Sirvoy booking system identifiers
        self.booking_token = "1420db72-b79c-4756-995a-00ec1fe760bc"
        self.property_id = "bf788a0e8c2e4631"
        self.container_id = "sbw_widget_1"
        
        # Get configuration from environment variables
        self.email_config = self._get_email_config()
        self.check_interval = int(os.getenv('CHECK_INTERVAL_MINUTES', '60'))
        
        # Store last known state
        self.last_available_dates = set()
        self.check_count = 0
        
        logger.info("🍇 Särtshöga Vingård Monitor initialized on Heroku")
        logger.info(f"📧 Email notifications: {'Enabled' if self.email_config else 'Disabled'}")
        logger.info(f"⏰ Check interval: {self.check_interval} minutes")
    
    def _get_email_config(self):
        """Get email configuration from environment variables"""
        smtp_server = os.getenv('SMTP_SERVER')
        smtp_port = os.getenv('SMTP_PORT')
        from_email = os.getenv('FROM_EMAIL')
        email_password = os.getenv('EMAIL_PASSWORD')
        to_email = os.getenv('TO_EMAIL')
        
        if all([smtp_server, smtp_port, from_email, email_password, to_email]):
            return {
                'smtp_server': smtp_server,
                'smtp_port': int(smtp_port),
                'from_email': from_email,
                'password': email_password,
                'to_email': to_email
            }
        else:
            logger.warning("Email configuration incomplete - notifications disabled")
            return None
    
    def make_request(self, url, **kwargs):
        """Make HTTP request with retries and error handling"""
        headers = kwargs.get('headers', {})
        headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        kwargs['headers'] = headers
        kwargs['timeout'] = 30
        
        for attempt in range(3):
            try:
                response = requests.get(url, **kwargs)
                response.raise_for_status()
                return response
            except requests.RequestException as e:
                if attempt == 2:
                    raise e
                logger.warning(f"Attempt {attempt + 1} failed, retrying...")
                time.sleep(2 ** attempt)
    
    def extract_sirvoy_data(self):
        """Extract data directly from Sirvoy booking widget"""
        try:
            # Direct Sirvoy booking widget URL
            sirvoy_widget_url = "https://secured.sirvoy.com/engine/book"
            
            # Parameters from the discovered URL
            widget_params = {
                't': 'a48dcdfb-b2e8-44cd-88d8-080c95b81a69',  # Updated token
                'id': 'bf788a0e8c2e4631',  # Property ID
                'container_id': 'sbw_widget_1'  # Container ID
            }
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'sv-SE,sv;q=0.9,en;q=0.8',
                'Referer': 'https://www.sartshogavingard.se/',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
            
            logger.info("🔍 Accessing direct Sirvoy booking widget...")
            
            # Get the booking widget directly
            response = self.make_request(sirvoy_widget_url, params=widget_params, headers=headers)
            
            logger.info(f"📄 Sirvoy widget loaded, size: {len(response.text)} characters")
            logger.info(f"📄 Content type: {response.headers.get('content-type', 'unknown')}")
            
            # Parse the widget HTML
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Method 1: Look for pageServerData in the widget
            page_data_div = soup.find('div', {'id': 'pageServerData'})
            if page_data_div:
                data_attr = page_data_div.get('data-page-server-data')
                if data_attr:
                    logger.info("✅ Found pageServerData in Sirvoy widget!")
                    decoded_data = html.unescape(data_attr)
                    sirvoy_data = json.loads(decoded_data)
                    logger.info(f"📊 Sirvoy data keys: {list(sirvoy_data.keys())}")
                    return sirvoy_data
            
            # Method 2: Look for JavaScript variables with booking data
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string:
                    script_content = script.string
                    
                    # Look for various booking data patterns
                    patterns = [
                        ('invalidCheckinDays', r'invalidCheckinDays["\']?\s*:\s*(\[.*?\])'),
                        ('blockedDates', r'blockedDates["\']?\s*:\s*(\[.*?\])'),
                        ('availableDates', r'availableDates["\']?\s*:\s*(\[.*?\])'),
                        ('bookingData', r'bookingData["\']?\s*:\s*(\{.*?\})'),
                        ('calendarData', r'calendarData["\']?\s*:\s*(\{.*?\})')
                    ]
                    
                    for pattern_name, pattern in patterns:
                        matches = re.findall(pattern, script_content, re.DOTALL)
                        if matches:
                            logger.info(f"✅ Found {pattern_name} in script!")
                            try:
                                data = json.loads(matches[0])
                                logger.info(f"📊 {pattern_name} data: {str(data)[:200]}...")
                                
                                # If we found invalidCheckinDays, build a response
                                if pattern_name == 'invalidCheckinDays':
                                    return {
                                        'invalidCheckinDays': json.dumps(data),
                                        'bookFromYear': datetime.now().year,
                                        'bookFromMonth': datetime.now().month,
                                        'bookFromDay': datetime.now().day,
                                        'bookUntilYear': datetime.now().year + 1,
                                        'bookUntilMonth': 12,
                                        'bookUntilDay': 31,
                                        '_source': 'sirvoy_widget_script'
                                    }
                            except json.JSONDecodeError:
                                logger.info(f"⚠️ Found {pattern_name} but couldn't parse as JSON")
                                continue
            
            # Method 3: Try to get calendar/availability data through API calls
            try:
                # Try different API endpoints that might return availability data
                api_endpoints = [
                    '/api/availability',
                    '/api/calendar', 
                    '/api/booking/availability',
                    '/engine/availability',
                    '/engine/calendar'
                ]
                
                base_api_url = 'https://secured.sirvoy.com'
                
                for endpoint in api_endpoints:
                    try:
                        api_url = base_api_url + endpoint
                        api_params = widget_params.copy()
                        
                        # Add common API parameters
                        api_params.update({
                            'from_date': datetime.now().strftime('%Y-%m-%d'),
                            'to_date': (datetime.now() + timedelta(days=365)).strftime('%Y-%m-%d'),
                            'format': 'json'
                        })
                        
                        logger.info(f"🔍 Trying API endpoint: {endpoint}")
                        
                        api_headers = headers.copy()
                        api_headers.update({
                            'Accept': 'application/json, text/javascript, */*; q=0.01',
                            'X-Requested-With': 'XMLHttpRequest'
                        })
                        
                        api_response = requests.get(api_url, params=api_params, headers=api_headers, timeout=15)
                        
                        if api_response.status_code == 200:
                            logger.info(f"✅ API endpoint {endpoint} responded successfully")
                            
                            try:
                                api_data = api_response.json()
                                logger.info(f"📊 API response keys: {list(api_data.keys()) if isinstance(api_data, dict) else 'not dict'}")
                                
                                # Check if this looks like availability data
                                if isinstance(api_data, dict):
                                    availability_keys = ['availability', 'calendar', 'dates', 'blocked', 'available']
                                    found_keys = [key for key in api_data.keys() if any(av_key in key.lower() for av_key in availability_keys)]
                                    
                                    if found_keys:
                                        logger.info(f"✅ Found availability-related keys: {found_keys}")
                                        return {
                                            'invalidCheckinDays': json.dumps(api_data.get('blocked_dates', api_data.get('blockedDates', []))),
                                            'bookFromYear': datetime.now().year,
                                            'bookFromMonth': datetime.now().month,
                                            'bookFromDay': datetime.now().day,
                                            'bookUntilYear': datetime.now().year + 1,
                                            'bookUntilMonth': 12,
                                            'bookUntilDay': 31,
                                            '_source': f'sirvoy_api_{endpoint}',
                                            '_raw_data': api_data
                                        }
                            
                            except json.JSONDecodeError:
                                logger.info(f"⚠️ API {endpoint} returned non-JSON data")
                                # Check if it's HTML with embedded data
                                if 'invalidCheckinDays' in api_response.text:
                                    logger.info(f"✅ Found invalidCheckinDays in HTML response from {endpoint}")
                                    api_soup = BeautifulSoup(api_response.content, 'html.parser')
                                    
                                    # Try to extract JSON from the HTML
                                    for script in api_soup.find_all('script'):
                                        if script.string and 'invalidCheckinDays' in script.string:
                                            try:
                                                start = script.string.find('{')
                                                end = script.string.rfind('}') + 1
                                                if start != -1 and end > start:
                                                    json_data = script.string[start:end]
                                                    parsed_data = json.loads(json_data)
                                                    logger.info(f"✅ Extracted JSON data from {endpoint}")
                                                    return parsed_data
                                            except:
                                                continue
                        
                        else:
                            logger.info(f"⚠️ API endpoint {endpoint} returned status {api_response.status_code}")
                    
                    except Exception as e:
                        logger.info(f"⚠️ API endpoint {endpoint} failed: {e}")
                        continue
            
            except Exception as e:
                logger.info(f"⚠️ API exploration failed: {e}")
            
            # Method 4: Analyze the widget structure for availability indicators
            logger.info("🔍 Analyzing widget structure...")
            
            # Look for form inputs that might indicate availability
            date_inputs = soup.find_all('input', type=['date', 'text'])
            select_elements = soup.find_all('select')
            buttons = soup.find_all(['button', 'input'], type=['submit', 'button'])
            
            # Look for calendar elements
            calendar_tables = soup.find_all('table')
            calendar_divs = soup.find_all('div', class_=lambda x: x and 'calendar' in str(x).lower())
            
            # Look for availability text
            widget_text = soup.get_text()
            availability_texts = []
            availability_keywords = [
                'tillgänglig', 'available', 'ledig', 'ledigt',
                'fullbokad', 'fully booked', 'unavailable',
                'välj datum', 'select date', 'choose date'
            ]
            
            for keyword in availability_keywords:
                if keyword in widget_text.lower():
                    availability_texts.append(keyword)
            
            logger.info(f"📊 Widget analysis:")
            logger.info(f"   - Date inputs: {len(date_inputs)}")
            logger.info(f"   - Select elements: {len(select_elements)}")
            logger.info(f"   - Buttons: {len(buttons)}")
            logger.info(f"   - Calendar tables: {len(calendar_tables)}")
            logger.info(f"   - Calendar divs: {len(calendar_divs)}")
            logger.info(f"   - Availability keywords found: {availability_texts}")
            
            # Create a hash of the widget content for change detection
            widget_hash = hash(widget_text)
            
            # Log some sample content for debugging
            if len(widget_text) > 100:
                logger.info(f"📄 Widget content sample: {widget_text[:200]}...")
            
            # Return widget state for change detection
            return {
                'invalidCheckinDays': '[]',  # Default to empty
                'bookFromYear': datetime.now().year,
                'bookFromMonth': datetime.now().month,
                'bookFromDay': datetime.now().day,
                'bookUntilYear': datetime.now().year + 1,
                'bookUntilMonth': 12,
                'bookUntilDay': 31,
                '_monitoring_mode': 'sirvoy_widget_monitoring',
                '_widget_hash': widget_hash,
                '_widget_size': len(response.text),
                '_date_inputs': len(date_inputs),
                '_select_elements': len(select_elements),
                '_buttons': len(buttons),
                '_calendar_elements': len(calendar_tables) + len(calendar_divs),
                '_availability_keywords': availability_texts,
                '_timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to access Sirvoy widget: {e}")
            
            # Fallback to main page monitoring if widget fails
            logger.info("🔄 Falling back to main page monitoring...")
            return self.extract_sirvoy_data_fallback()
    
    def extract_sirvoy_data_fallback(self):
        """Fallback method if direct widget access fails"""
        try:
            response = self.make_request(self.base_url)
            page_hash = hash(response.text)
            
            return {
                'invalidCheckinDays': '[]',
                'bookFromYear': datetime.now().year,
                'bookFromMonth': datetime.now().month,
                'bookFromDay': datetime.now().day,
                'bookUntilYear': datetime.now().year + 1,
                'bookUntilMonth': 12,
                'bookUntilDay': 31,
                '_monitoring_mode': 'fallback_page_monitoring',
                '_page_hash': page_hash,
                '_page_size': len(response.text),
                '_timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"❌ Fallback monitoring also failed: {e}")
            raise
    
    def analyze_availability(self, sirvoy_data):
        """Analyze availability from direct Sirvoy widget data"""
        try:
            monitoring_mode = sirvoy_data.get('_monitoring_mode', 'unknown')
            
            # Handle direct Sirvoy widget data
            if monitoring_mode == 'sirvoy_widget_monitoring':
                return self.analyze_widget_data(sirvoy_data)
            
            # Handle successful API data extraction
            elif sirvoy_data.get('_source', '').startswith('sirvoy_'):
                return self.analyze_real_sirvoy_data(sirvoy_data)
            
            # Handle fallback page monitoring
            elif monitoring_mode == 'fallback_page_monitoring':
                return self.analyze_page_changes(sirvoy_data)
            
            # Default case
            else:
                logger.info(f"🔄 Using default analysis for mode: {monitoring_mode}")
                return self.analyze_real_sirvoy_data(sirvoy_data)
                
        except Exception as e:
            logger.error(f"❌ Error in analyze_availability: {e}")
            return [], 1
    
    def analyze_real_sirvoy_data(self, sirvoy_data):
        """Debug and analyze real Sirvoy availability data structure"""
        try:
            logger.info("✅ Analyzing REAL Sirvoy availability data!")
            
            # First, let's dump ALL the raw data to understand the structure
            logger.info("📊 RAW SIRVOY DATA DUMP:")
            for key, value in sirvoy_data.items():
                if isinstance(value, str) and len(value) > 100:
                    logger.info(f"   {key}: {value[:100]}... (truncated)")
                else:
                    logger.info(f"   {key}: {value}")
            
            # Parse the invalidCheckinDays
            invalid_checkin_days_raw = sirvoy_data.get('invalidCheckinDays', '[]')
            logger.info(f"📅 invalidCheckinDays raw: {invalid_checkin_days_raw[:200]}...")
            
            try:
                invalid_checkin_days = json.loads(invalid_checkin_days_raw)
                logger.info(f"📅 Parsed invalidCheckinDays: {len(invalid_checkin_days)} blocked dates")
                
                # Show some samples
                if invalid_checkin_days:
                    logger.info(f"   First 5 blocked: {invalid_checkin_days[:5]}")
                    logger.info(f"   Last 5 blocked: {invalid_checkin_days[-5:]}")
                    
                    # Check July dates specifically
                    july_blocked = [date for date in invalid_checkin_days if date.startswith('2025-07')]
                    logger.info(f"   July 2025 blocked: {len(july_blocked)} dates")
                    if july_blocked:
                        logger.info(f"   July blocked sample: {july_blocked[:10]}")
                    
                    # Check specifically for July 11th
                    july_11 = "2025-07-11"
                    if july_11 in invalid_checkin_days:
                        logger.info(f"❌ July 11th ({july_11}) is in BLOCKED list")
                    else:
                        logger.info(f"✅ July 11th ({july_11}) is NOT in blocked list")
                
            except json.JSONDecodeError as e:
                logger.error(f"❌ Could not parse invalidCheckinDays: {e}")
                invalid_checkin_days = []
            
            # Look for other availability-related fields
            logger.info("🔍 Looking for other availability fields:")
            
            # Check allowedStays
            allowed_stays_raw = sirvoy_data.get('allowedStays', '[]')
            logger.info(f"📅 allowedStays raw: {allowed_stays_raw[:200]}...")
            
            try:
                allowed_stays = json.loads(allowed_stays_raw)
                logger.info(f"📅 Parsed allowedStays: {len(allowed_stays)} entries")
                
                # Count non-zero entries (these might indicate availability)
                available_count = 0
                for i, stay in enumerate(allowed_stays):
                    if stay and stay != 0 and stay != '0':
                        available_count += 1
                        if available_count <= 10:  # Show first 10
                            logger.info(f"   Day {i}: allowed stays = {stay}")
                
                logger.info(f"📊 Days with allowed stays: {available_count}")
                
            except json.JSONDecodeError as e:
                logger.error(f"❌ Could not parse allowedStays: {e}")
            
            # Check defaultAllowedStays
            default_allowed = sirvoy_data.get('defaultAllowedStays', '')
            logger.info(f"📅 defaultAllowedStays: {default_allowed}")
            
            # Check jsUserData
            js_user_data_raw = sirvoy_data.get('jsUserData', '{}')
            logger.info(f"📅 jsUserData raw: {js_user_data_raw}")
            
            try:
                js_user_data = json.loads(js_user_data_raw)
                logger.info(f"📅 Parsed jsUserData: {js_user_data}")
            except:
                logger.info("⚠️ Could not parse jsUserData")
            
            # Look for booking period info
            try:
                book_from_year = int(sirvoy_data.get('bookFromYear', datetime.now().year))
                book_from_month = int(sirvoy_data.get('bookFromMonth', datetime.now().month))
                book_from_day = int(sirvoy_data.get('bookFromDay', datetime.now().day))
                
                book_until_year = int(sirvoy_data.get('bookUntilYear', datetime.now().year + 1))
                book_until_month = int(sirvoy_data.get('bookUntilMonth', 12))
                book_until_day = int(sirvoy_data.get('bookUntilDay', 31))
                
                start_date = datetime(book_from_year, book_from_month, book_from_day)
                end_date = datetime(book_until_year, book_until_month, book_until_day)
                
                logger.info(f"📅 Booking period: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
                
                # Calculate total days in booking period
                total_days = (end_date - start_date).days + 1
                logger.info(f"📊 Total days in booking period: {total_days}")
                logger.info(f"📊 Blocked days: {len(invalid_checkin_days)}")
                logger.info(f"📊 Theoretical available days: {total_days - len(invalid_checkin_days)}")
                
            except Exception as e:
                logger.error(f"❌ Error calculating booking period: {e}")
            
            # NEW APPROACH: Look for the actual availability data structure
            # Maybe the availability is encoded differently
            
            # Check if there's a pattern in allowedStays that shows real availability
            try:
                allowed_stays = json.loads(sirvoy_data.get('allowedStays', '[]'))
                
                # Map allowedStays to actual dates
                available_dates_from_stays = []
                if allowed_stays:
                    current_date = datetime(book_from_year, book_from_month, book_from_day)
                    
                    for i, stay_option in enumerate(allowed_stays):
                        date_str = current_date.strftime('%Y-%m-%d')
                        
                        # Check if this day allows any stays
                        if stay_option and stay_option != 0:
                            available_dates_from_stays.append(date_str)
                            if len(available_dates_from_stays) <= 5:  # Log first few
                                logger.info(f"   Available from allowedStays: {date_str} (stays: {stay_option})")
                        
                        current_date += timedelta(days=1)
                        if current_date > end_date:
                            break
                    
                    logger.info(f"✅ REAL AVAILABILITY from allowedStays: {len(available_dates_from_stays)} dates")
                    
                    # Check July 11th in this method
                    july_11 = "2025-07-11"
                    if july_11 in available_dates_from_stays:
                        logger.info(f"✅ July 11th ({july_11}) found in allowedStays method!")
                    
                    return available_dates_from_stays, total_days - len(available_dates_from_stays)
                
            except Exception as e:
                logger.error(f"❌ Error analyzing allowedStays: {e}")
            
            # Fallback to original method but log the discrepancy
            blocked_dates = set(invalid_checkin_days)
            available_dates = []
            
            current_date = max(start_date, datetime.now())
            while current_date <= end_date:
                date_str = current_date.strftime('%Y-%m-%d')
                if date_str not in blocked_dates:
                    available_dates.append(date_str)
                current_date += timedelta(days=1)
            
            logger.info(f"⚠️ FALLBACK METHOD shows {len(available_dates)} available dates")
            logger.info(f"⚠️ This doesn't match reality - there's probably a different availability structure")
            
            return available_dates, len(blocked_dates)
            
        except Exception as e:
            logger.error(f"❌ Error in debug analysis: {e}")
            return [], 1
    
    def analyze_widget_data(self, sirvoy_data):
        """Analyze Sirvoy widget structure for changes"""
        try:
            current_widget_hash = sirvoy_data.get('_widget_hash')
            current_widget_size = sirvoy_data.get('_widget_size')
            current_date_inputs = sirvoy_data.get('_date_inputs', 0)
            current_select_elements = sirvoy_data.get('_select_elements', 0)
            current_buttons = sirvoy_data.get('_buttons', 0)
            current_calendar_elements = sirvoy_data.get('_calendar_elements', 0)
            current_availability_keywords = sirvoy_data.get('_availability_keywords', [])
            
            # Check if this is the first run
            if not hasattr(self, '_last_widget_hash'):
                self._last_widget_hash = current_widget_hash
                self._last_widget_size = current_widget_size
                self._last_date_inputs = current_date_inputs
                self._last_select_elements = current_select_elements
                self._last_buttons = current_buttons
                self._last_calendar_elements = current_calendar_elements
                self._last_availability_keywords = current_availability_keywords
                
                logger.info("📊 Widget baseline established")
                logger.info(f"   - Widget hash: {abs(current_widget_hash) % 1000000}")
                logger.info(f"   - Widget size: {current_widget_size}")
                logger.info(f"   - Interactive elements: {current_date_inputs + current_select_elements + current_buttons}")
                logger.info(f"   - Calendar elements: {current_calendar_elements}")
                logger.info(f"   - Availability keywords: {current_availability_keywords}")
                logger.info("   - Status: Monitoring Sirvoy widget for changes")
                
                return [], 1  # No availability detected initially
            
            # Check for changes in the widget
            widget_hash_changed = current_widget_hash != self._last_widget_hash
            size_changed = abs(current_widget_size - self._last_widget_size) > 100
            inputs_changed = current_date_inputs != self._last_date_inputs
            selects_changed = current_select_elements != self._last_select_elements
            buttons_changed = current_buttons != self._last_buttons
            calendar_changed = current_calendar_elements != self._last_calendar_elements
            keywords_changed = set(current_availability_keywords) != set(self._last_availability_keywords)
            
            # Count significant widget changes
            widget_changes = sum([
                widget_hash_changed,
                size_changed,
                inputs_changed,
                selects_changed, 
                buttons_changed,
                calendar_changed,
                keywords_changed
            ])
            
            if widget_changes >= 2:  # Require multiple changes for confidence
                logger.info("🎉 SIGNIFICANT WIDGET CHANGES DETECTED!")
                logger.info(f"   - Widget content changed: {widget_hash_changed}")
                logger.info(f"   - Size changed: {size_changed} ({self._last_widget_size} → {current_widget_size})")
                logger.info(f"   - Date inputs changed: {inputs_changed} ({self._last_date_inputs} → {current_date_inputs})")
                logger.info(f"   - Select elements changed: {selects_changed} ({self._last_select_elements} → {current_select_elements})")
                logger.info(f"   - Buttons changed: {buttons_changed} ({self._last_buttons} → {current_buttons})")
                logger.info(f"   - Calendar elements changed: {calendar_changed} ({self._last_calendar_elements} → {current_calendar_elements})")
                logger.info(f"   - Keywords changed: {keywords_changed}")
                logger.info(f"   - Total changes: {widget_changes}/7")
                
                # Update baseline
                self._last_widget_hash = current_widget_hash
                self._last_widget_size = current_widget_size
                self._last_date_inputs = current_date_inputs
                self._last_select_elements = current_select_elements
                self._last_buttons = current_buttons
                self._last_calendar_elements = current_calendar_elements
                self._last_availability_keywords = current_availability_keywords
                
                # Return change notification
                change_date = datetime.now().strftime('%Y-%m-%d')
                return [change_date], 0
                
            else:
                logger.info("📊 No significant widget changes detected")
                logger.info(f"   - Widget changes: {widget_changes}/7 (threshold: 2)")
                logger.info(f"   - Current hash: {abs(current_widget_hash) % 1000000}")
                logger.info(f"   - Interactive elements: {current_date_inputs + current_select_elements + current_buttons}")
                
                return [], 1  # No changes
                
        except Exception as e:
            logger.error(f"❌ Error analyzing widget data: {e}")
            return [], 1
    
    def analyze_page_changes(self, sirvoy_data):
        """Fallback page change analysis"""
        try:
            current_hash = sirvoy_data.get('_page_hash')
            current_size = sirvoy_data.get('_page_size')
            
            if not hasattr(self, '_fallback_page_hash'):
                self._fallback_page_hash = current_hash
                self._fallback_page_size = current_size
                
                logger.info("📊 Fallback page monitoring baseline established")
                return [], 1
            
            hash_changed = current_hash != self._fallback_page_hash
            size_changed = abs(current_size - self._fallback_page_size) > 500
            
            if hash_changed or size_changed:
                logger.info("🔍 Page change detected in fallback mode")
                
                self._fallback_page_hash = current_hash
                self._fallback_page_size = current_size
                
                change_date = datetime.now().strftime('%Y-%m-%d')
                return [change_date], 0
            
            return [], 1
            
        except Exception as e:
            logger.error(f"❌ Error in fallback analysis: {e}")
            return [], 1
    
    def send_notification(self, subject, message):
        """Send email notification"""
        logger.info(f"🔔 {subject}")
        logger.info(f"📋 {message}")
        
        if not self.email_config:
            return
            
        try:
            msg = MIMEMultipart()
            msg['From'] = self.email_config['from_email']
            msg['To'] = self.email_config['to_email']
            msg['Subject'] = subject
            
            email_body = f"""
Hej!

{message}

🔗 Boka här: {self.base_url}

⏰ Kontrollerad: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
📊 Totala kontroller: {self.check_count}
☁️ Kör på Heroku

// Automatisk Sirvoy-övervakning av Särtshöga Vingård
            """
            
            msg.attach(MIMEText(email_body, 'plain', 'utf-8'))
            
            with smtplib.SMTP(self.email_config['smtp_server'], self.email_config['smtp_port']) as server:
                server.starttls()
                server.login(self.email_config['from_email'], self.email_config['password'])
                server.send_message(msg)
                
            logger.info(f"📧 E-post skickad till {self.email_config['to_email']}")
            
        except Exception as e:
            logger.error(f"❌ Kunde inte skicka e-post: {e}")
    
    def check_availability(self):
        """Main availability checking function with improved change detection logic"""
        self.check_count += 1
        current_time = datetime.now()
        
        logger.info(f"🔍 Kontroll #{self.check_count}")
        
        try:
            sirvoy_data = self.extract_sirvoy_data()
            available_dates, blocked_count = self.analyze_availability(sirvoy_data)
            
            # Better logging based on monitoring mode
            monitoring_mode = sirvoy_data.get('_monitoring_mode', 'unknown')
            
            if monitoring_mode == 'sirvoy_widget_monitoring':
                if len(available_dates) > 0:
                    logger.info(f"🎉 WIDGET CHANGE DETECTED! Potential availability update")
                    logger.info(f"📊 Widget change notification triggered")
                else:
                    logger.info(f"📊 Widget monitoring: No changes detected")
            elif sirvoy_data.get('_source', '').startswith('sirvoy_'):
                logger.info(f"📊 Real Sirvoy data: {len(available_dates)} tillgängliga dagar, {blocked_count} blockerade")
            else:
                logger.info(f"📊 Monitoring: {len(available_dates)} changes detected")
            
            if available_dates:
                current_available = set(available_dates)
                
                # For widget monitoring, always treat changes as potential availability
                if monitoring_mode == 'sirvoy_widget_monitoring':
                    if self.check_count > 1:  # Skip notifications on first run (baseline)
                        logger.info(f"🎉 SIRVOY WIDGET CHANGE NOTIFICATION")
                        
                        self.send_notification(
                            "🍇 Särtshöga Vingård - Bokningswidget har ändrats!",
                            f"Sirvoy bokningswidgeten för Särtshöga Vingård har ändrats!\n\n" +
                            f"Detta kan betyda att nya rum har blivit tillgängliga.\n\n" +
                            f"Kontrollera manuellt: {self.base_url}\n\n" +
                            f"Kontroll #{self.check_count} genomförd {current_time.strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                    else:
                        logger.info("📊 Första widget-kontrollen - ingen notifikation skickas")
                
                # For real data mode, use standard logic with actual dates
                elif sirvoy_data.get('_source', '').startswith('sirvoy_'):
                    logger.info(f"✅ Real availability data found!")
                    logger.info(f"   Sample available dates: {', '.join(sorted(available_dates[:5]))}")
                    
                    if self.last_available_dates:
                        new_dates = current_available - self.last_available_dates
                        
                        if new_dates:
                            new_dates_list = sorted(list(new_dates))
                            logger.info(f"🎉 NYA TILLGÄNGLIGA DAGAR: {', '.join(new_dates_list)}")
                            
                            self.send_notification(
                                "🍇 Nya rum tillgängliga på Särtshöga Vingård!",
                                f"Nya tillgängliga dagar:\n" + 
                                "\n".join([f"📅 {date}" for date in new_dates_list])
                            )
                    
                    self.last_available_dates = current_available
                
                # For fallback page monitoring
                else:
                    if self.check_count > 1:  # Skip notifications on first run
                        logger.info(f"🎉 PAGE CHANGE NOTIFICATION (Fallback mode)")
                        
                        self.send_notification(
                            "🍇 Särtshöga Vingård - Sidan har ändrats!",
                            f"Webbsidan för Särtshöga Vingård har ändrats!\n\n" +
                            f"Detta kan betyda att nya rum har blivit tillgängliga.\n\n" +
                            f"Kontrollera manuellt: {self.base_url}\n\n" +
                            f"Kontroll #{self.check_count} genomförd {current_time.strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                    else:
                        logger.info("📊 Första fallback-kontrollen - ingen notifikation skickas")
                
                return True
                
            else:
                if monitoring_mode == 'sirvoy_widget_monitoring':
                    logger.info("📊 Widget-övervakning aktiv - väntar på ändringar...")
                elif sirvoy_data.get('_source', '').startswith('sirvoy_'):
                    logger.info("📊 Real Sirvoy data: Inga tillgängliga dagar för närvarande")
                else:
                    logger.info("📊 Övervakning aktiv - väntar på ändringar...")
                return False
                
        except Exception as e:
            logger.error(f"❌ Fel vid kontroll #{self.check_count}: {e}")
            return False
    
    def run_forever(self):
        """Run continuous monitoring for Heroku"""
        logger.info("🚀 Startar kontinuerlig övervakning på Heroku")
        
        # Run first check immediately
        self.check_availability()
        
        # Keep checking at intervals
        while True:
            try:
                time.sleep(self.check_interval * 60)  # Convert minutes to seconds
                self.check_availability()
            except KeyboardInterrupt:
                logger.info("⏹️ Övervakning stoppad")
                break
            except Exception as e:
                logger.error(f"❌ Oväntat fel: {e}")
                time.sleep(300)  # Wait 5 minutes before retrying

def main():
    """Main function for Heroku"""
    monitor = HerokuSartshogaMonitor()
    
    # For Heroku scheduler or one-off dyno
    if os.getenv('HEROKU_SCHEDULER'):
        logger.info("🕐 Kör som Heroku Scheduler job")
        monitor.check_availability()
    else:
        # For continuous running dyno
        logger.info("♾️ Kör som kontinuerlig process")
        monitor.run_forever()

if __name__ == "__main__":
    main()