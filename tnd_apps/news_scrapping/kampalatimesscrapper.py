import requests
from bs4 import BeautifulSoup
import time
import re
from datetime import datetime
from django.utils import timezone
from urllib.parse import urljoin
from django.utils.text import slugify
from .models import Article, Category, Tag, Author, NewsSource, ScrapingRun, ScrapingLog

class KampalaTimesDjangoScraper:
    def __init__(self, source_name="Kampala Edge Times"):
        try:
            self.source = NewsSource.objects.get(name=source_name)
        except NewsSource.DoesNotExist:
            # Create default source if it doesn't exist
            self.source = NewsSource.objects.create(
                name=source_name,
                base_url="https://www.kampalaedgetimes.com",
                news_url="https://www.kampalaedgetimes.com/kampala-edge-times/kampala-edge-times-articles"
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
    
        # Use slug as the lookup field since it has the unique constraint
        category_slug = slugify(category_name)
        category, created = Category.objects.get_or_create(
            slug=category_slug,  # Use slug for lookup
            defaults={'name': category_name}
        )
        return category

    def get_or_create_tag(self, tag_name):
        """Get or create a tag"""
        if not tag_name:
            return None
    
        tag_slug = slugify(tag_name)
        tag, created = Tag.objects.get_or_create(
            slug=tag_slug,  # Use slug for lookup
            defaults={'name': tag_name}
        )
        return tag

    def get_or_create_author(self, author_name, profile_url=""):
        """Get or create an author"""
        # Default to "Guest" if author_name is empty or None
        author_name = author_name or "Guest"
        author, created = Author.objects.get_or_create(
            name=author_name,
            source=self.source,
            defaults={'profile_url': profile_url}
        )
        return author

    def extract_article_data(self, article_element):
        """Extract data from a single article element"""
        try:
            data = {}
    
            # Extract post ID from article tag
            article_id = article_element.get('id', '')
            if article_id:
                data['external_id'] = article_id.replace('post-', '')
    
            # Extract title and URL from content div
            content_div = article_element.find('div', class_='content')
            if content_div:
                title_element = content_div.find('h2', class_='is-title post-title')
                if title_element:
                    title_link = title_element.find('a')
                    if title_link:
                        data['title'] = title_link.get_text(strip=True)
                        data['url'] = title_link.get('href', '')
    
            # Extract featured image from media div
            media_div = article_element.find('div', class_='media')
            if media_div:
                img_span = media_div.find('span', class_='img')
                if img_span:
                    data['featured_image'] = img_span.get('data-bgsrc', '')
    
            # Extract category from cat-labels span
            cat_labels = article_element.find('span', class_='cat-labels')
            if cat_labels:
                category_link = cat_labels.find('a')
                data['category'] = category_link.get_text(strip=True) if category_link else ''
    
            # Extract date and author from post-meta
            post_meta = content_div.find('div', class_='post-meta') if content_div else None
            if post_meta:
                date_element = post_meta.find('time', class_='post-date')
                if date_element:
                    data['published_time'] = date_element.get_text(strip=True)
                    data['published_datetime'] = date_element.get('datetime', '')
                
                author_element = post_meta.find('span', class_='post-author')
                if author_element:
                    author_link = author_element.find('a')
                    if author_link:
                        data['author'] = author_link.get_text(strip=True)
                        data['author_url'] = author_link.get('href', '')
    
            # Extract excerpt
            excerpt_div = content_div.find('div', class_='excerpt') if content_div else None
            if excerpt_div:
                excerpt_p = excerpt_div.find('p')
                data['excerpt'] = excerpt_p.get_text(strip=True) if excerpt_p else ''
    
            return data
    
        except Exception as e:
            print(f"Error extracting article data: {str(e)}")
            return None

    def scrape_full_article_content(self, article_url, run):
        """Scrape full content from individual article page"""
        try:
            response = self.session.get(article_url, timeout=30)
            response.raise_for_status()
    
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find the main content element
            main_content = soup.find('div', class_='main-content')
            if not main_content:
                self.log_message(run, 'warning', f'No main content found for {article_url}', article_url)
                return None
    
            article_data = {}
    
            # Extract title from h1 in the-post-header
            header = main_content.find('div', class_='the-post-header')
            if header:
                title_element = header.find('h1', class_='is-title')
                article_data['full_title'] = title_element.get_text(strip=True) if title_element else ''
                
                # Extract author from post-meta
                post_meta = header.find('div', class_='post-meta')
                if post_meta:
                    author_span = post_meta.find('span', class_='post-author')
                    if author_span:
                        author_link = author_span.find('a')
                        if author_link:
                            article_data['author'] = author_link.get_text(strip=True)
                            article_data['author_url'] = author_link.get('href', '')
    
            # Extract featured image from single-featured div
            featured_div = main_content.find('div', class_='single-featured')
            if featured_div:
                img_element = featured_div.find('img')
                if img_element:
                    article_data['featured_image_url'] = img_element.get('src', '')
                    article_data['image_caption'] = img_element.get('alt', '')
    
            # Extract main content
            content_div = main_content.find('div', class_='post-content')
            full_content = []
    
            if content_div:
                # Find all paragraphs, excluding those in unwanted containers
                for p in content_div.find_all('p'):
                    # Skip paragraphs inside specific containers
                    if p.find_parent(class_=['post-share', 'sharedaddy', 'jp-relatedposts', 'code-block']):
                        continue
                    
                    # Skip empty paragraphs or those with only &nbsp;
                    text = p.get_text(strip=True)
                    if text and text != '' and len(text) > 10:
                        # Clean up whitespace
                        text = re.sub(r'\s+', ' ', text)
                        full_content.append(text)
    
            # Extract tags from the-post-tags
            tags_div = main_content.find('div', class_='the-post-tags')
            tags = []
            if tags_div:
                tag_links = tags_div.find_all('a', rel='tag')
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
            blog_entries = soup.find('div', class_='loop loop-grid')

            if not blog_entries:
                raise Exception("Could not find blog entries area")

            # Find all article containers
            article_containers = blog_entries.find_all('article', class_='l-post')
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
                        article_data.get('author', ''),
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
                            
                            # Update author from full content if available
                            if full_data.get('author'):
                                article.author = self.get_or_create_author(
                                    full_data['author'],
                                    full_data.get('author_url', '')
                                )

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
