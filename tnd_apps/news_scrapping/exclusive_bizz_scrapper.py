import requests
from bs4 import BeautifulSoup
import time
import re
from datetime import datetime
from django.utils import timezone
from urllib.parse import urljoin, urlparse
from django.utils.text import slugify
from .models import Article, Category, Tag, Author, NewsSource, ScrapingRun, ScrapingLog


class ExclusiveCoUgScraper:
    """
    Enhanced scraper for Exclusive.co.ug news website

    - Better error handling with retry logic
    - More efficient content extraction
    - Automatic excerpt generation from article body
    - Better HTML cleaning and text extraction
    - More robust date parsing
    - Connection pooling with session management
    """

    def __init__(self, source_name="Exclusive.co.ug"):
        try:
            self.source = NewsSource.objects.get(name=source_name)
        except NewsSource.DoesNotExist:
            self.source = NewsSource.objects.create(
                name=source_name,
                base_url="https://exclusive.co.ug",
                news_url="https://exclusive.co.ug/category/news/"
            )

        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Cache-Control': 'max-age=0',
        }

        # Initialize session with connection pooling
        self.session = requests.Session()
        self.session.headers.update(self.headers)

        # Configure session for better performance
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=3
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def log_message(self, run, level, message, article_url=""):
        """Log a message to the database"""
        ScrapingLog.objects.create(
            run=run,
            level=level,
            message=message,
            article_url=article_url
        )

    def get_or_create_category(self, category_name):
        """Get or create a category with proper slug handling"""
        if not category_name or not category_name.strip():
            return None

        category_name = category_name.strip()
        category_slug = slugify(category_name)

        category, created = Category.objects.get_or_create(
            slug=category_slug,
            defaults={'name': category_name}
        )
        return category

    def get_or_create_tag(self, tag_name):
        """Get or create a tag with proper slug handling"""
        if not tag_name or not tag_name.strip():
            return None

        tag_name = tag_name.strip()
        tag_slug = slugify(tag_name)

        tag, created = Tag.objects.get_or_create(
            slug=tag_slug,
            defaults={'name': tag_name}
        )
        return tag

    def get_or_create_author(self, author_name, profile_url=""):
        """Get or create an author"""
        if not author_name or not author_name.strip():
            return None

        author_name = author_name.strip()
        author, created = Author.objects.get_or_create(
            name=author_name,
            source=self.source,
            defaults={'profile_url': profile_url}
        )
        return author

    def parse_date(self, date_string):
        """
        Parse date string into datetime object
        Handles formats like "January 10, 2026"
        """
        if not date_string:
            return None

        try:
            date_string = date_string.strip()
            # Try parsing "Month Day, Year" format
            parsed_date = datetime.strptime(date_string, "%B %d, %Y")
            return timezone.make_aware(parsed_date)
        except ValueError:
            try:
                # Try alternative format
                parsed_date = datetime.strptime(date_string, "%B %d, %Y")
                return timezone.make_aware(parsed_date)
            except ValueError:
                return None

    def clean_text(self, text):
        """Clean and normalize text content"""
        if not text:
            return ""

        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        # Remove special characters that might cause issues
        text = text.strip()
        return text

    def extract_article_list_data(self, article_element):
        """
        Extract data from article list item
        More robust extraction with fallbacks
        """
        try:
            data = {}

            # Extract post ID from classes
            classes = article_element.get('class', [])
            post_id = None
            for cls in classes:
                if cls.startswith('post-') and cls[5:].isdigit():
                    post_id = cls
                    break
            data['external_id'] = post_id or ''

            # Extract title and URL
            title_element = article_element.select_one('h3.elementor-post__title a')
            if title_element:
                data['title'] = self.clean_text(title_element.get_text())
                data['url'] = title_element.get('href', '').strip()

            # Extract featured image
            img_element = article_element.select_one('.elementor-post__thumbnail img')
            if img_element:
                # Try different image attributes
                img_url = (img_element.get('src') or
                           img_element.get('data-src') or
                           img_element.get('data-lazy-src', ''))
                data['featured_image'] = img_url.strip()
                data['image_alt'] = img_element.get('alt', '').strip()

            # Extract date
            date_element = article_element.select_one('.elementor-post-date')
            if date_element:
                data['published_date'] = self.clean_text(date_element.get_text())

            # Extract category from hentry classes
            for cls in classes:
                if cls.startswith('category-') and cls != 'category-news':
                    category_name = cls.replace('category-', '').replace('-', ' ').title()
                    data['category'] = category_name
                    break

            # Extract tags from hentry classes
            tags = []
            for cls in classes:
                if cls.startswith('tag-'):
                    tag_name = cls.replace('tag-', '').replace('-', ' ').title()
                    tags.append(tag_name)
            data['tags'] = tags

            return data

        except Exception as e:
            return None

    def extract_excerpt_from_content(self, content_paragraphs, max_length=200):
        """
        Generate excerpt from article content
        Takes first few sentences up to max_length
        """
        if not content_paragraphs:
            return ""

        excerpt = []
        current_length = 0

        for paragraph in content_paragraphs:
            if current_length >= max_length:
                break

            # Split into sentences
            sentences = re.split(r'[.!?]+', paragraph)
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue

                if current_length + len(sentence) > max_length:
                    # Add partial sentence if we have room
                    if current_length < max_length * 0.7:  # At least 70% of max
                        excerpt.append(sentence[:max_length - current_length] + "...")
                    break

                excerpt.append(sentence)
                current_length += len(sentence)

                if current_length >= max_length:
                    break

        return ". ".join(excerpt) + "." if excerpt else ""

    def scrape_full_article_content(self, article_url, run):
        """
        Scrape full content from individual article page
        Enhanced with better content extraction and error handling
        """
        retry_count = 0
        max_retries = 3

        while retry_count < max_retries:
            try:
                response = self.session.get(article_url, timeout=30)
                response.raise_for_status()

                soup = BeautifulSoup(response.content, 'html.parser')
                article_data = {}

                # Extract title
                title_element = soup.select_one('h1.elementor-heading-title')
                if title_element:
                    article_data['full_title'] = self.clean_text(title_element.get_text())

                # Extract featured image
                featured_img = soup.select_one('.elementor-widget-theme-post-featured-image img')
                if featured_img:
                    img_url = (featured_img.get('src') or
                               featured_img.get('data-src') or
                               featured_img.get('data-lazy-src', ''))
                    article_data['featured_image_url'] = img_url.strip()
                    article_data['image_caption'] = featured_img.get('alt', '').strip()

                # Extract author and date from post info
                author_element = soup.select_one('.elementor-post-info__item--type-author')
                if author_element:
                    author_link = author_element.find('a')
                    if author_link:
                        article_data['author'] = self.clean_text(author_link.get_text())
                        article_data['author_url'] = author_link.get('href', '').strip()

                date_element = soup.select_one('.elementor-post-info__item--type-date time')
                if date_element:
                    article_data['published_date_str'] = self.clean_text(date_element.get_text())

                # Extract main content
                content_div = soup.select_one('.elementor-widget-theme-post-content')
                full_content = []

                if content_div:
                    # Get all paragraphs
                    paragraphs = content_div.find_all('p')

                    for p in paragraphs:
                        # Skip social sharing, ads, and embedded content
                        if (p.find_parent(class_='elementor-share-btn') or
                                p.find_parent(class_='twitter-tweet') or
                                p.find_parent(class_='wp-block-embed') or
                                p.find_parent('script') or
                                p.find_parent('style')):
                            continue

                        text = self.clean_text(p.get_text())

                        # Only include substantial paragraphs
                        if len(text) > 20:
                            full_content.append(text)

                # Extract tags from the tags section
                tags = []
                tags_element = soup.select_one('.elementor-widget-text-editor')
                if tags_element:
                    tag_links = tags_element.find_all('a', rel='tag')
                    tags = [self.clean_text(tag.get_text()) for tag in tag_links]

                # Generate excerpt from content if we have it
                excerpt = self.extract_excerpt_from_content(full_content, max_length=200)

                article_data.update({
                    'full_content': '\n\n'.join(full_content),
                    'excerpt': excerpt,
                    'word_count': len(' '.join(full_content).split()),
                    'paragraph_count': len(full_content),
                    'tags': tags,
                })

                return article_data

            except requests.exceptions.RequestException as e:
                retry_count += 1
                if retry_count >= max_retries:
                    self.log_message(run, 'error',
                                     f'Failed after {max_retries} retries: {str(e)}',
                                     article_url)
                    return None

                # Exponential backoff
                wait_time = 2 ** retry_count
                time.sleep(wait_time)

            except Exception as e:
                self.log_message(run, 'error',
                                 f'Error scraping full content: {str(e)}',
                                 article_url)
                return None

        return None

    def scrape_and_save(self, get_full_content=True, max_articles=None, start_page=1, max_pages=1):
        """
        Main method to scrape and save articles to database

        Args:
            get_full_content: Whether to fetch full article content
            max_articles: Maximum number of articles to scrape (None for all)
            start_page: Page number to start scraping from
            max_pages: Maximum number of pages to scrape
        """
        # Create scraping run record
        run = ScrapingRun.objects.create(
            source=self.source,
            status='started'
        )

        try:
            self.log_message(run, 'info', f'Started scraping from {self.source.news_url}')

            total_articles_processed = 0

            for page in range(start_page, start_page + max_pages):
                # Construct page URL
                if page == 1:
                    page_url = self.source.news_url
                else:
                    page_url = f"{self.source.news_url}/page/{page}/"

                self.log_message(run, 'info', f'Scraping page {page}: {page_url}')

                # Get page content
                response = self.session.get(page_url, timeout=30)
                print(page_url)
                print(response.content)
                response.raise_for_status()

                soup = BeautifulSoup(response.content, 'html.parser')

                # Find all article containers using the Elementor structure
                article_containers = soup.select('article.elementor-post.elementor-grid-item')

                if not article_containers:
                    self.log_message(run, 'warning', f'No articles found on page {page}')
                    break

                run.articles_found += len(article_containers)
                run.save()

                self.log_message(run, 'info',
                                 f'Found {len(article_containers)} articles on page {page}')

                for i, container in enumerate(article_containers):
                    # Check if we've hit max_articles limit
                    if max_articles and total_articles_processed >= max_articles:
                        self.log_message(run, 'info',
                                         f'Reached max_articles limit ({max_articles})')
                        break

                    try:
                        # Extract basic article data
                        article_data = self.extract_article_list_data(container)

                        if not article_data or not article_data.get('url'):
                            run.articles_skipped += 1
                            self.log_message(run, 'warning',
                                             f'Skipped article {i + 1} on page {page}: No URL found')
                            continue

                        # Check if article already exists
                        existing_article = Article.objects.filter(
                            url=article_data['url']
                        ).first()

                        if existing_article:
                            # Update if we need full content and don't have it
                            if get_full_content and not existing_article.has_full_content:
                                full_data = self.scrape_full_article_content(
                                    article_data['url'], run
                                )

                                if full_data:
                                    existing_article.content = full_data['full_content']
                                    existing_article.excerpt = full_data['excerpt']
                                    existing_article.word_count = full_data['word_count']
                                    existing_article.paragraph_count = full_data['paragraph_count']
                                    existing_article.image_caption = full_data.get('image_caption', '')
                                    existing_article.has_full_content = True

                                    # Update author if we have better data
                                    if full_data.get('author'):
                                        author = self.get_or_create_author(
                                            full_data['author'],
                                            full_data.get('author_url', '')
                                        )
                                        if author:
                                            existing_article.author = author

                                    existing_article.save()

                                    # Update tags
                                    if full_data.get('tags'):
                                        existing_article.tags.clear()
                                        for tag_name in full_data['tags']:
                                            tag = self.get_or_create_tag(tag_name)
                                            if tag:
                                                existing_article.tags.add(tag)

                                    run.articles_updated += 1
                                    self.log_message(run, 'info',
                                                     f'Updated article: {existing_article.title}')
                            else:
                                run.articles_skipped += 1

                            total_articles_processed += 1
                            continue

                        # Create new article
                        category = self.get_or_create_category(
                            article_data.get('category')
                        )

                        # Create article object
                        article = Article(
                            external_id=article_data.get('external_id', ''),
                            url=article_data['url'],
                            title=article_data.get('title', ''),
                            featured_image_url=article_data.get('featured_image', ''),
                            source=self.source,
                            category=category,
                            published_time_str=article_data.get('published_date', ''),
                        )

                        # Parse and set published date
                        if article_data.get('published_date'):
                            parsed_date = self.parse_date(article_data['published_date'])
                            if parsed_date:
                                article.published_at = parsed_date

                        # Get full content if requested
                        full_data = None
                        if get_full_content:
                            full_data = self.scrape_full_article_content(
                                article_data['url'], run
                            )

                            if full_data:
                                article.content = full_data['full_content']
                                article.excerpt = full_data['excerpt']
                                article.word_count = full_data['word_count']
                                article.paragraph_count = full_data['paragraph_count']
                                article.image_caption = full_data.get('image_caption', '')
                                article.has_full_content = True

                                # Set author from full data if available
                                if full_data.get('author'):
                                    author = self.get_or_create_author(
                                        full_data['author'],
                                        full_data.get('author_url', '')
                                    )
                                    if author:
                                        article.author = author

                        # Save article
                        article.save()

                        # Add tags (prefer full_data tags, fallback to list tags)
                        tags_to_add = []
                        if full_data and full_data.get('tags'):
                            tags_to_add = full_data['tags']
                        elif article_data.get('tags'):
                            tags_to_add = article_data['tags']

                        for tag_name in tags_to_add:
                            tag = self.get_or_create_tag(tag_name)
                            if tag:
                                article.tags.add(tag)

                        run.articles_added += 1
                        total_articles_processed += 1
                        self.log_message(run, 'info', f'Added new article: {article.title}')

                        # Respectful delay between requests
                        time.sleep(1)

                    except Exception as e:
                        run.error_count += 1
                        self.log_message(run, 'error',
                                         f'Error processing article {i + 1} on page {page}: {str(e)}')
                        continue

                # Check if we should stop
                if max_articles and total_articles_processed >= max_articles:
                    break

                # Delay between pages
                if page < start_page + max_pages - 1:
                    time.sleep(2)

            # Mark run as completed
            run.status = 'completed'
            run.completed_at = timezone.now()
            run.save()

            summary_msg = (f'Scraping completed. '
                           f'Added: {run.articles_added}, '
                           f'Updated: {run.articles_updated}, '
                           f'Skipped: {run.articles_skipped}, '
                           f'Errors: {run.error_count}')
            self.log_message(run, 'info', summary_msg)

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

    def __del__(self):
        """Clean up session on object destruction"""
        if hasattr(self, 'session'):
            self.session.close()