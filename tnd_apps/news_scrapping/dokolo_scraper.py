import requests
from bs4 import BeautifulSoup
import time
import re
from datetime import datetime
from django.utils import timezone
from urllib.parse import urljoin
from django.utils.text import slugify
from .models import Article, Category, Tag, Author, NewsSource, ScrapingRun, ScrapingLog


class DokoloPostDjangoScraper:
    def __init__(self, source_name="Dokolo Post"):
        try:
            self.source = NewsSource.objects.get(name=source_name)
        except NewsSource.DoesNotExist:
            # Create default source if it doesn't exist
            self.source = NewsSource.objects.create(
                name=source_name,
                base_url="https://dokolopost.com",
                news_url="https://dokolopost.com"
            )

        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

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

        category, created = Category.objects.get_or_create(
            name=category_name,
            defaults={'slug': slugify(category_name)}
        )
        return category

    def get_or_create_tag(self, tag_name):
        """Get or create a tag"""
        if not tag_name:
            return None

        tag, created = Tag.objects.get_or_create(
            name=tag_name,
            defaults={'slug': slugify(tag_name)}
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

    def extract_article_data(self, article_div):
        """Extract data from a single article div element"""
        try:
            data = {}

            # Extract post ID from the post div classes
            post_div = article_div.find('div', class_=lambda x: x and 'post-' in x)
            if post_div:
                classes = post_div.get('class', [])
                post_classes = [cls for cls in classes if cls.startswith('post-')]
                data['external_id'] = post_classes[0] if post_classes else ''

            # Extract title and URL
            title_element = article_div.find('h2', class_='entry-title')
            if title_element:
                title_link = title_element.find('a')
                if title_link:
                    data['title'] = title_link.get_text(strip=True)
                    data['url'] = title_link.get('href', '')

            # Extract featured image
            img_element = article_div.find('figure', class_='post-featured-image')
            if img_element:
                img_tag = img_element.find('img')
                if img_tag:
                    data['featured_image'] = img_tag.get('src', '')

            # Extract category
            category_element = article_div.find('div', class_='cat-links')
            if category_element:
                category_link = category_element.find('a')
                data['category'] = category_link.get_text(strip=True) if category_link else ''

            # Extract date and author from entry-meta
            entry_meta = article_div.select_one("div.entry-meta:not(.category-meta)")
            if entry_meta:
                # Extract date
                date_element = entry_meta.find('div', class_='date')
                if date_element:
                    date_link = date_element.find('a')
                    data['published_time'] = date_link.get_text(strip=True) if date_link else ''

                # Extract author
                author_element = entry_meta.find('div', class_='by-author')
                if author_element:
                    author_link = author_element.find('a')
                    if author_link:
                        data['author'] = author_link.get_text(strip=True)
                        data['author_url'] = author_link.get('href', '')

            # Extract excerpt
            content_element = article_div.find('div', class_='entry-content')
            if content_element:
                excerpt_p = content_element.find('p')
                data['excerpt'] = excerpt_p.get_text(strip=True) if excerpt_p else ''

            return data

        except Exception as e:
            return None

    def scrape_full_article_content(self, article_url, run):
        """Scrape full content from individual article page"""
        try:
            response = self.session.get(article_url, timeout=30)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')
            main_element = soup.find('main', id='main')

            if not main_element:
                self.log_message(run, 'warning', f'No main content found for {article_url}', article_url)
                return None

            article_data = {}

            # Extract title
            title_element = main_element.find('h1', class_='entry-title')
            article_data['full_title'] = title_element.get_text(strip=True) if title_element else ''

            # Extract featured image
            featured_img = main_element.find('figure', class_='post-featured-image')
            if featured_img:
                img_tag = featured_img.find('img')
                if img_tag:
                    article_data['featured_image_url'] = img_tag.get('src', '')
                    article_data['image_caption'] = img_tag.get('alt', '')

            # Extract content
            content_div = main_element.find('div', class_='entry-content')
            full_content = []

            if content_div:
                paragraphs = content_div.find_all('p')
                for p in paragraphs:
                    # Skip social sharing buttons and ads
                    if (p.find_parent(class_='sd-sharing') or
                            p.find_parent(class_='wordads-tag') or
                            p.find_parent(class_='sharedaddy')):
                        continue

                    text = p.get_text(strip=True)
                    if len(text) > 10:
                        # Clean up text
                        text = re.sub(r'\s+', ' ', text)
                        full_content.append(text)

            # Extract tags from footer
            tags = []
            footer_meta = main_element.find('footer', class_='entry-meta')
            if footer_meta:
                tag_links = footer_meta.find_all('a', rel='tag')
                tags = [tag.get_text(strip=True) for tag in tag_links]

            article_data.update({
                'full_content': '\n\n'.join(full_content),
                'word_count': len(' '.join(full_content).split()),
                'paragraph_count': len(full_content),
                'tags': tags,
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

            # Get main news page
            response = self.session.get(self.source.news_url, timeout=30)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')
            main_element = soup.find('main', id='main')

            if not main_element:
                raise Exception("Could not find main content area")

            # Find all article containers
            article_containers = main_element.find_all('div', class_='col-sm-6 col-xxl-4 post-col')
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
                    author = self.get_or_create_author(
                        article_data.get('author'),
                        article_data.get('author_url', '')
                    )

                    article = Article(
                        external_id=article_data.get('external_id', ''),
                        url=article_data['url'],
                        title=article_data.get('title', ''),
                        excerpt=article_data.get('excerpt', ''),
                        featured_image_url=article_data.get('featured_image', ''),
                        source=self.source,
                        category=category,
                        author=author,
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
                    time.sleep(1)

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