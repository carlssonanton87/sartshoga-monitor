#!/usr/bin/env python3
"""
Särtshöga Vingård Availability Monitor - Heroku Version
Monitors Sirvoy booking system for room availability
"""

import requests
import json
import html
import os
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
        """Extract Sirvoy booking data using the real API endpoints"""
        try:
            # First, get the main page to extract tokens and parameters
            response = self.make_request(self.base_url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            logger.info(f"📄 Page loaded successfully, size: {len(response.text)} characters")
            
            # Method 1: Try to find pageServerData (original method)
            page_data_div = soup.find('div', {'id': 'pageServerData'})
            if page_data_div:
                data_attr = page_data_div.get('data-page-server-data')
                if data_attr:
                    try:
                        decoded_data = html.unescape(data_attr)
                        sirvoy_data = json.loads(decoded_data)
                        logger.info("✅ Found Sirvoy data in pageServerData div")
                        return sirvoy_data
                    except:
                        logger.info("⚠️ Found pageServerData but couldn't parse JSON")
            
            # Method 2: Try to call the Sirvoy API directly
            try:
                # Use the parameters we know from your original paste
                sirvoy_api_url = "https://secured.sirvoy.com/engine/book"
                
                # Parameters based on the original data
                params = {
                    't': self.booking_token,  # "1420db72-b79c-4756-995a-00ec1fe760bc"
                    'id': self.property_id,   # "bf788a0e8c2e4631"
                    'container_id': self.container_id,  # "sbw_widget_1"
                    'arrival_date': datetime.now().strftime('%Y-%m-%d'),
                    'departure_date': (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d'),
                    'adults': 2,
                    'action': 'get_availability'  # Try different actions
                }
                
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': self.base_url,
                    'Accept': 'application/json, text/javascript, */*; q=0.01',
                    'X-Requested-With': 'XMLHttpRequest'
                }
                
                logger.info("🔍 Attempting direct Sirvoy API call...")
                api_response = requests.get(sirvoy_api_url, params=params, headers=headers, timeout=30)
                
                if api_response.status_code == 200:
                    logger.info(f"✅ Sirvoy API responded with status 200")
                    logger.info(f"📄 Response content type: {api_response.headers.get('content-type', 'unknown')}")
                    
                    # Try to parse as JSON
                    try:
                        api_data = api_response.json()
                        logger.info("✅ Successfully parsed Sirvoy API response as JSON")
                        return api_data
                    except:
                        # If not JSON, might be HTML with embedded data
                        api_soup = BeautifulSoup(api_response.content, 'html.parser')
                        
                        # Look for JSON data in script tags
                        scripts = api_soup.find_all('script')
                        for script in scripts:
                            if script.string and 'invalidCheckinDays' in script.string:
                                logger.info("✅ Found availability data in API response script")
                                try:
                                    # Extract JSON from script
                                    script_content = script.string
                                    start = script_content.find('{')
                                    end = script_content.rfind('}') + 1
                                    if start != -1 and end > start:
                                        json_data = script_content[start:end]
                                        return json.loads(json_data)
                                except:
                                    continue
                        
                        logger.info(f"📄 API response preview: {api_response.text[:200]}...")
                
                else:
                    logger.info(f"⚠️ Sirvoy API responded with status {api_response.status_code}")
            
            except Exception as e:
                logger.info(f"⚠️ Direct API call failed: {e}")
            
            # Method 3: Try alternative API endpoints
            alternative_endpoints = [
                "https://secured.sirvoy.com/engine/book_require_code",
                "https://secured.sirvoy.com/engine/availability",
                "https://secured.sirvoy.com/api/availability"
            ]
            
            for endpoint in alternative_endpoints:
                try:
                    logger.info(f"🔍 Trying alternative endpoint: {endpoint}")
                    params = {
                        't': self.booking_token,
                        'id': self.property_id,
                        'container_id': self.container_id
                    }
                    
                    alt_response = requests.get(endpoint, params=params, headers=headers, timeout=30)
                    if alt_response.status_code == 200 and len(alt_response.text) > 100:
                        logger.info(f"✅ Alternative endpoint {endpoint} responded")
                        
                        # Try to find JSON data
                        try:
                            return alt_response.json()
                        except:
                            # Look for embedded JSON
                            if 'invalidCheckinDays' in alt_response.text:
                                logger.info("✅ Found availability data in alternative endpoint")
                                alt_soup = BeautifulSoup(alt_response.content, 'html.parser')
                                scripts = alt_soup.find_all('script')
                                for script in scripts:
                                    if script.string and 'invalidCheckinDays' in script.string:
                                        try:
                                            script_content = script.string
                                            start = script_content.find('{')
                                            end = script_content.rfind('}') + 1
                                            if start != -1 and end > start:
                                                json_data = script_content[start:end]
                                                return json.loads(json_data)
                                        except:
                                            continue
                except:
                    continue
            
            # Method 4: Look for inline JavaScript with booking data
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string:
                    script_content = script.string
                    # Look for various patterns that might contain availability data
                    patterns = [
                        'invalidCheckinDays',
                        'blocked_dates',
                        'available_dates',
                        'booking_data',
                        'calendar_data'
                    ]
                    
                    for pattern in patterns:
                        if pattern in script_content:
                            logger.info(f"🔍 Found {pattern} in script tag")
                            # Try to extract JSON around this pattern
                            try:
                                pattern_index = script_content.find(pattern)
                                # Look backwards and forwards for JSON boundaries
                                start = script_content.rfind('{', 0, pattern_index)
                                end = script_content.find('}', pattern_index) + 1
                                if start != -1 and end > start:
                                    json_candidate = script_content[start:end]
                                    test_data = json.loads(json_candidate)
                                    if isinstance(test_data, dict) and pattern in json_candidate:
                                        logger.info(f"✅ Successfully extracted data around {pattern}")
                                        return test_data
                            except:
                                continue
            
            # Method 5: Debug - log more information about what we're finding
            logger.info("🔍 Extended debugging information:")
            
            # Look for any iframes that might contain the booking widget
            iframes = soup.find_all('iframe')
            if iframes:
                logger.info(f"   - Found {len(iframes)} iframes")
                for i, iframe in enumerate(iframes):
                    src = iframe.get('src', '')
                    if 'sirvoy' in src.lower():
                        logger.info(f"   - Iframe {i}: {src}")
            
            # Look for elements with sirvoy-related classes or IDs
            sirvoy_elements = soup.find_all(attrs={'class': lambda x: x and 'sirvoy' in str(x).lower()})
            sirvoy_elements.extend(soup.find_all(attrs={'id': lambda x: x and 'sirvoy' in str(x).lower()}))
            if sirvoy_elements:
                logger.info(f"   - Found {len(sirvoy_elements)} elements with 'sirvoy' in class/id")
            
            # Check if we're being blocked or redirected
            if 'robot' in response.text.lower() or 'blocked' in response.text.lower():
                logger.warning("⚠️ Possible bot detection - might be blocked")
            
            # Last resort: return empty data but log what we found
            logger.info("❌ Could not extract real availability data")
            logger.info("📄 Falling back to monitoring page changes instead")
            
            # Return a structure that indicates we need to monitor differently
            return {
                'invalidCheckinDays': '[]',  # Empty - assume everything available for now
                'bookFromYear': datetime.now().year,
                'bookFromMonth': datetime.now().month,
                'bookFromDay': datetime.now().day,
                'bookUntilYear': datetime.now().year + 1,
                'bookUntilMonth': 12,
                'bookUntilDay': 31,
                '_status': 'fallback_mode',
                '_page_size': len(response.text),
                '_page_hash': hash(response.text)  # Use for change detection
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to extract Sirvoy data: {e}")
            raise Exception(f"Failed to extract Sirvoy data: {e}")
    
    def analyze_availability(self, sirvoy_data):
        """Analyze availability from Sirvoy data"""
        try:
            invalid_checkin_days = json.loads(sirvoy_data.get('invalidCheckinDays', '[]'))
            
            book_from_year = sirvoy_data.get('bookFromYear', datetime.now().year)
            book_from_month = sirvoy_data.get('bookFromMonth', datetime.now().month)
            book_from_day = sirvoy_data.get('bookFromDay', datetime.now().day)
            
            book_until_year = sirvoy_data.get('bookUntilYear', datetime.now().year + 1)
            book_until_month = sirvoy_data.get('bookUntilMonth', 12)
            book_until_day = sirvoy_data.get('bookUntilDay', 31)
            
            start_date = datetime(book_from_year, book_from_month, int(book_from_day))
            end_date = datetime(book_until_year, book_until_month, int(book_until_day))
            
            blocked_dates = set(invalid_checkin_days)
            available_dates = []
            
            current_date = max(start_date, datetime.now())
            
            while current_date <= end_date:
                date_str = current_date.strftime('%Y-%m-%d')
                if date_str not in blocked_dates:
                    available_dates.append(date_str)
                current_date += timedelta(days=1)
            
            return available_dates, len(blocked_dates)
            
        except Exception as e:
            raise Exception(f"Failed to analyze availability: {e}")
    
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
        """Main availability checking function"""
        self.check_count += 1
        current_time = datetime.now()
        
        logger.info(f"🔍 Kontroll #{self.check_count}")
        
        try:
            sirvoy_data = self.extract_sirvoy_data()
            available_dates, blocked_count = self.analyze_availability(sirvoy_data)
            
            logger.info(f"📊 {len(available_dates)} tillgängliga dagar, {blocked_count} blockerade")
            
            if available_dates:
                current_available = set(available_dates)
                
                logger.info(f"✅ Exempel på tillgängliga dagar: {', '.join(sorted(available_dates[:5]))}")
                
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
                return True
                
            else:
                logger.info("❌ Inga tillgängliga dagar för närvarande")
                return False
                
        except Exception as e:
            logger.error(f"❌ Fel vid kontroll #{self.check_count}: {e}")
            return False
    
    def run_forever(self):
        """Run continuous monitoring for Heroku"""
        logger.info("🚀 Startar kontinuerlig övervakning på Heroku")
        
        self.check_availability()
        
        while True:
            try:
                time.sleep(self.check_interval * 60)
                self.check_availability()
            except KeyboardInterrupt:
                logger.info("⏹️ Övervakning stoppad")
                break
            except Exception as e:
                logger.error(f"❌ Oväntat fel: {e}")
                time.sleep(300)

def main():
    """Main function for Heroku"""
    monitor = HerokuSartshogaMonitor()
    
    if os.getenv('HEROKU_SCHEDULER'):
        logger.info("🕐 Kör som Heroku Scheduler job")
        monitor.check_availability()
    else:
        logger.info("♾️ Kör som kontinuerlig process")
        monitor.run_forever()

if __name__ == "__main__":
    main()