from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import time
import re
from datetime import datetime
from django.utils import timezone
from urllib.parse import urljoin
from django.utils.text import slugify
from .models import Article, Category, Tag, Author, NewsSource, ScrapingRun, ScrapingLog
import hashlib


class MonitorNewsDjangoScraper:
    def __init__(self, source_name="Daily Monitor"):
        try:
            self.source = NewsSource.objects.get(name=source_name)
        except NewsSource.DoesNotExist:
            # Create default source if it doesn't exist
            self.source = NewsSource.objects.create(
                name=source_name,
                base_url="https://www.monitor.co.ug",
                news_url="https://www.monitor.co.ug/uganda/news"
            )

        self.base_url = "https://www.monitor.co.ug"
        self.driver = None

    def setup_selenium_driver(self):
        """Setup Selenium Chrome driver with options to bypass Cloudflare"""
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        chrome_options.add_argument(f'user-agent={user_agent}')

        self.driver = webdriver.Chrome(options=chrome_options)
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    def cleanup_selenium_driver(self):
        """Close the Selenium driver"""
        if self.driver:
            self.driver.quit()
            self.driver = None

    def log_message(self, run, level, message, article_url=""):
        """Log a message to the database"""
        ScrapingLog.objects.create(
            run=run,
            level=level,
            message=message,
            article_url=article_url
        )

    def get_or_create_category(self, category_name):
        """Get or create a category"""
        if not category_name:
            return None

        category_slug = slugify(category_name)
        category, created = Category.objects.get_or_create(
            slug=category_slug,
            defaults={'name': category_name}
        )
        return category

    def get_or_create_tag(self, tag_name):
        """Get or create a tag"""
        if not tag_name:
            return None

        tag_slug = slugify(tag_name)
        tag, created = Tag.objects.get_or_create(
            slug=tag_slug,
            defaults={'name': tag_name}
        )
        return tag

    def get_or_create_author(self, author_name, profile_url=""):
        """Get or create an author"""
        if not author_name:
            return None

        author, created = Author.objects.get_or_create(
            name=author_name,
            source=self.source,
            defaults={'profile_url': profile_url}
        )
        return author

    def normalize_url(self, url):
        """Convert relative URLs to absolute URLs"""
        if url.startswith('/'):
            return self.base_url + url
        return url

    def extract_article_data(self, article_item):
        """Extract data from a single article list item element"""
        try:
            data = {}

            # Extract article URL and title
            link_element = article_item.find('a', class_='teaser-image-large')
            if not link_element:
                return None

            data['url'] = self.normalize_url(link_element.get('href', ''))
            data['aria_label'] = link_element.get('aria-label', '')

            # Extract title
            title_element = article_item.find('h3', class_='teaser-image-large_title')
            data['title'] = title_element.get_text(strip=True) if title_element else 'No title'

            # Extract category/topic
            topic_element = article_item.find('span', class_='article-topic')
            data['category'] = topic_element.get_text(strip=True) if topic_element else ''

            # Extract date
            date_element = article_item.find('span', class_='date')
            data['published_time'] = date_element.get_text(strip=True) if date_element else ''

            # Extract featured image
            img_element = article_item.find('img')
            if img_element:
                img_src = img_element.get('src', '')
                data['featured_image'] = self.normalize_url(img_src)
            else:
                data['featured_image'] = ''

            # Generate external_id from URL
            if data['url']:
                extracted_id = data['url'].split('/')[-1]
                data['external_id'] = hashlib.sha1(extracted_id.encode()).hexdigest()[:8]
            else:
                data['external_id'] = ''

            return data

        except Exception as e:
            return None

    def scrape_full_article_content(self, article_url, run):
        """Scrape full content from individual article page using Selenium"""
        try:
            self.driver.get(article_url)

            # Wait for page to load
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(2)

            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            article_data = {}

            # Extract title (might be different from listing page)
            title_element = soup.find('h1') or soup.find('h1', class_='article-title')
            article_data['full_title'] = title_element.get_text(strip=True) if title_element else ''

            # Extract author
            author_element = soup.find('span', class_='author') or soup.find('div', class_='author')
            if author_element:
                author_text = author_element.get_text(strip=True)
                # Clean author text (remove "By " prefix if present)
                article_data['author'] = re.sub(r'^By\s+', '', author_text, flags=re.IGNORECASE)
            else:
                article_data['author'] = ''

            # Extract content paragraphs
            content_selectors = [
                'div.article-content p',
                'div.story-content p',
                'div.entry-content p',
                '.article-body p'
            ]

            full_content = []
            for selector in content_selectors:
                paragraphs = soup.select(selector)
                if paragraphs:
                    for p in paragraphs:
                        text = p.get_text(strip=True)
                        if len(text) > 20:  # Filter out very short paragraphs
                            text = re.sub(r'\s+', ' ', text)
                            full_content.append(text)
                    break

            # If no content found with selectors, try finding all paragraphs in main content area
            if not full_content:
                main_content = soup.find('main') or soup.find('article') or soup.find('div', class_=lambda
                    x: x and 'content' in x.lower())
                if main_content:
                    paragraphs = main_content.find_all('p')
                    for p in paragraphs:
                        text = p.get_text(strip=True)
                        if len(text) > 20:
                            text = re.sub(r'\s+', ' ', text)
                            full_content.append(text)

            # Extract tags (look for common tag patterns)
            tags = []
            tag_selectors = [
                '.tags a',
                '.article-tags a',
                '.post-tags a',
                'a[rel="tag"]'
            ]

            for selector in tag_selectors:
                tag_elements = soup.select(selector)
                if tag_elements:
                    tags = [tag.get_text(strip=True) for tag in tag_elements]
                    break

            # Extract image caption
            img_caption = ''
            caption_selectors = [
                'figcaption',
                '.image-caption',
                '.wp-caption-text',
                '.caption'
            ]

            for selector in caption_selectors:
                caption_element = soup.select_one(selector)
                if caption_element:
                    img_caption = caption_element.get_text(strip=True)
                    break

            article_data.update({
                'full_content': '\n\n'.join(full_content),
                'word_count': len(' '.join(full_content).split()),
                'paragraph_count': len(full_content),
                'tags': tags,
                'image_caption': img_caption
            })

            return article_data

        except Exception as e:
            self.log_message(run, 'error', f'Error scraping full content: {str(e)}', article_url)
            return None

    def scrape_and_save(self, get_full_content=True, max_articles=None):
        """Main method to scrape and save articles to database"""

        # Create scraping run record
        run = ScrapingRun.objects.create(
            source=self.source,
            status='started'
        )

        try:
            self.log_message(run, 'info', f'Started scraping from {self.source.news_url}')

            # Setup Selenium driver
            self.setup_selenium_driver()

            # Navigate to news page
            self.driver.get(self.source.news_url)

            # Wait for page to load
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(3)  # Additional wait for content to load

            # Parse page with BeautifulSoup
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')

            # Find all news article containers
            article_containers = soup.find_all('li', class_=lambda x: x and 'col-1-1' in x)
            run.articles_found = len(article_containers)
            run.save()

            self.log_message(run, 'info', f'Found {len(article_containers)} articles')

            if max_articles:
                article_containers = article_containers[:max_articles]

            for i, container in enumerate(article_containers):
                try:
                    # Extract basic article data
                    article_data = self.extract_article_data(container)
                    if not article_data or not article_data.get('url'):
                        run.articles_skipped += 1
                        continue

                    # Check if article already exists
                    existing_article = Article.objects.filter(
                        url=article_data['url']
                    ).first()

                    if existing_article:
                        # Update if we have more complete data
                        if get_full_content and not existing_article.has_full_content:
                            full_data = self.scrape_full_article_content(article_data['url'], run)
                            if full_data:
                                existing_article.content = full_data['full_content']
                                existing_article.word_count = full_data['word_count']
                                existing_article.paragraph_count = full_data['paragraph_count']
                                existing_article.image_caption = full_data.get('image_caption', '')
                                existing_article.has_full_content = True

                                # Update author if we found one
                                if full_data.get('author'):
                                    author = self.get_or_create_author(full_data['author'])
                                    existing_article.author = author

                                existing_article.save()

                                # Add tags
                                for tag_name in full_data.get('tags', []):
                                    tag = self.get_or_create_tag(tag_name)
                                    if tag:
                                        existing_article.tags.add(tag)

                                run.articles_updated += 1
                                self.log_message(run, 'info', f'Updated article: {existing_article.title}')
                        else:
                            run.articles_skipped += 1
                        continue

                    # Create new article
                    category = self.get_or_create_category(article_data.get('category'))

                    article = Article(
                        external_id=article_data.get('external_id', ''),
                        url=article_data['url'],
                        title=article_data.get('title', ''),
                        excerpt='',  # Monitor doesn't seem to have excerpts in listing
                        featured_image_url=article_data.get('featured_image', ''),
                        source=self.source,
                        category=category,
                        published_time_str=article_data.get('published_time', ''),
                    )

                    # Get full content if requested
                    if get_full_content:
                        full_data = self.scrape_full_article_content(article_data['url'], run)
                        if full_data:
                            article.content = full_data['full_content']
                            article.word_count = full_data['word_count']
                            article.paragraph_count = full_data['paragraph_count']
                            article.image_caption = full_data.get('image_caption', '')
                            article.has_full_content = True

                            # Set author from full content
                            if full_data.get('author'):
                                author = self.get_or_create_author(full_data['author'])
                                article.author = author

                    article.save()

                    # Add tags
                    if get_full_content and full_data:
                        for tag_name in full_data.get('tags', []):
                            tag = self.get_or_create_tag(tag_name)
                            if tag:
                                article.tags.add(tag)

                    run.articles_added += 1
                    self.log_message(run, 'info', f'Added new article: {article.title}')

                    # Respectful delay
                    time.sleep(2)

                except Exception as e:
                    run.error_count += 1
                    self.log_message(run, 'error', f'Error processing article {i + 1}: {str(e)}')
                    continue

            # Mark run as completed
            run.status = 'completed'
            run.completed_at = timezone.now()
            run.save()

            self.log_message(run, 'info',
                             f'Scraping completed. Added: {run.articles_added}, Updated: {run.articles_updated}, Skipped: {run.articles_skipped}')

            return {
                'run_id': run.run_id,
                'articles_found': run.articles_found,
                'articles_added': run.articles_added,
                'articles_updated': run.articles_updated,
                'articles_skipped': run.articles_skipped,
                'errors': run.error_count,
                'duration': run.duration_seconds
            }

        except Exception as e:
            run.status = 'failed'
            run.error_message = str(e)
            run.completed_at = timezone.now()
            run.save()

            self.log_message(run, 'error', f'Scraping failed: {str(e)}')
            raise

        finally:
            # Always cleanup the driver
            self.cleanup_selenium_driver()

# Usage example:
# scraper = MonitorNewsDjangoScraper()
# results = scraper.scrape_and_save(get_full_content=True, max_articles=10)
# print(f"Scraping completed: {results}")