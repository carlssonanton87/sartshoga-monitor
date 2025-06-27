# heroku_monitor.py - Modified for Heroku deployment
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
        self.check_interval = int(os.getenv('CHECK_INTERVAL_MINUTES', '60'))  # Default 1 hour
        
        # Store last known state (in production, you'd use a database)
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
        """Extract Sirvoy booking data from the website with better error handling"""
        try:
            response = self.make_request(self.base_url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            logger.info(f"📄 Page loaded successfully, size: {len(response.text)} characters")
            
            # Method 1: Look for pageServerData div
            page_data_div = soup.find('div', {'id': 'pageServerData'})
            if page_data_div:
                data_attr = page_data_div.get('data-page-server-data')
                if data_attr:
                    decoded_data = html.unescape(data_attr)
                    sirvoy_data = json.loads(decoded_data)
                    logger.info("✅ Found Sirvoy data in pageServerData div")
                    return sirvoy_data
            
            # Method 2: Look for any script tag containing Sirvoy data
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string and 'sirvoy' in script.string.lower():
                    logger.info("🔍 Found script tag with Sirvoy reference")
                    # Try to extract JSON data from script
                    script_content = script.string
                    if 'invalidCheckinDays' in script_content:
                        logger.info("✅ Found invalidCheckinDays in script")
                        # Extract the JSON part (this might need adjustment)
                        try:
                            start = script_content.find('{')
                            end = script_content.rfind('}') + 1
                            if start != -1 and end > start:
                                json_data = script_content[start:end]
                                sirvoy_data = json.loads(json_data)
                                return sirvoy_data
                        except:
                            continue
            
            # Method 3: Look for iframe or other Sirvoy elements
            sirvoy_elements = soup.find_all(attrs={'class': lambda x: x and 'sirvoy' in str(x).lower()})
            if sirvoy_elements:
                logger.info(f"🔍 Found {len(sirvoy_elements)} elements with 'sirvoy' in class")
            
            # Method 4: Check for any data attributes containing booking info
            data_elements = soup.find_all(attrs={'data-booking': True})
            data_elements.extend(soup.find_all(attrs={'data-calendar': True}))
            data_elements.extend(soup.find_all(attrs={'data-availability': True}))
            
            if data_elements:
                logger.info(f"🔍 Found {len(data_elements)} elements with booking-related data attributes")
                for elem in data_elements:
                    for attr, value in elem.attrs.items():
                        if 'data-' in attr and len(str(value)) > 50:  # Likely contains JSON
                            try:
                                decoded_data = html.unescape(str(value))
                                test_data = json.loads(decoded_data)
                                if isinstance(test_data, dict) and len(test_data) > 3:
                                    logger.info(f"✅ Found booking data in {attr}")
                                    return test_data
                            except:
                                continue
            
            # Method 5: Log what we found for debugging
            logger.info("🔍 Debugging information:")
            logger.info(f"   - Page title: {soup.title.string if soup.title else 'No title'}")
            logger.info(f"   - Total divs: {len(soup.find_all('div'))}")
            logger.info(f"   - Total scripts: {len(soup.find_all('script'))}")
            
            # Look for any mentions of booking or availability
            text_content = soup.get_text().lower()
            booking_indicators = ['boka', 'booking', 'tillgänglig', 'available', 'calendar']
            found_indicators = [word for word in booking_indicators if word in text_content]
            logger.info(f"   - Booking indicators found: {found_indicators}")
            
            # Check if the booking widget might be loaded dynamically
            if 'sirvoy' in response.text.lower():
                logger.info("✅ Page contains 'sirvoy' text - widget might load dynamically")
                # Return a minimal structure to indicate we found the page
                return {
                    'invalidCheckinDays': '[]',  # Empty for now
                    'bookFromYear': datetime.now().year,
                    'bookFromMonth': datetime.now().month,
                    'bookFromDay': datetime.now().day,
                    'bookUntilYear': datetime.now().year + 1,
                    'bookUntilMonth': 12,
                    'bookUntilDay': 31,
                    '_status': 'widget_detected_but_data_not_accessible'
                }
            
            raise ValueError("No Sirvoy booking data found on page")
            
        except Exception as e:
            logger.error(f"❌ Failed to extract Sirvoy data: {e}")
            # Log the first 500 characters of the page for debugging
            try:
                logger.error(f"📄 Page preview: {response.text[:500]}...")
            except:
                pass
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
                
                # Log some available dates
                logger.info(f"✅ Exempel på tillgängliga dagar: {', '.join(sorted(available_dates[:5]))}")
                
                # Check for new availability
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