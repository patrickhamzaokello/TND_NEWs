import requests
from bs4 import BeautifulSoup
import time
import re
from datetime import datetime
from django.utils import timezone
from urllib.parse import urljoin
from django.utils.text import slugify
from .models import Article, Category, Tag, Author, NewsSource, ScrapingRun, ScrapingLog

class TNDNewsDjangoScraper:
    def __init__(self, source_name="TND News Uganda"):
        try:
            self.source = NewsSource.objects.get(name=source_name)
        except NewsSource.DoesNotExist:
            # Create default source if it doesn't exist
            self.source = NewsSource.objects.create(
                name=source_name,
                base_url="https://tndnewsuganda.com",
                news_url="https://tndnewsuganda.com"
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
        if not author_name:
            return None

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
                data['external_id'] = article_id
    
            # Extract title and URL from entry-header
            header = article_element.find('header', class_='entry-header')
            if header:
                title_element = header.find('h2', class_='entry-title')
                if title_element:
                    title_link = title_element.find('a')
                    if title_link:
                        data['title'] = title_link.get_text(strip=True)
                        data['url'] = title_link.get('href', '')
    
            # Extract featured image from post-thumbnail div
            thumbnail_div = article_element.find('div', class_='post-thumbnail')
            if thumbnail_div:
                img_element = thumbnail_div.find('img')
                if img_element:
                    # Try multiple image attributes
                    data['featured_image'] = (
                        img_element.get('src') or 
                        img_element.get('data-src') or 
                        img_element.get('data-lazy-src') or
                        ''
                    )
    
            # Extract category from cat-links span
            cat_links = article_element.find('span', class_='cat-links')
            if cat_links:
                category_link = cat_links.find('a')
                data['category'] = category_link.get_text(strip=True) if category_link else ''
    
            # Extract date from entry-meta
            entry_meta = article_element.find('div', class_='entry-meta')
            if entry_meta:
                posted_on = entry_meta.find('span', class_='posted-on')
                if posted_on:
                    time_element = posted_on.find('time', class_='entry-date')
                    if time_element:
                        data['published_time'] = time_element.get_text(strip=True)
                        # Also get datetime attribute if available
                        data['published_datetime'] = time_element.get('datetime', '')
    
            # Extract excerpt from entry-content
            content_div = article_element.find('div', class_='entry-content')
            if content_div:
                excerpt_p = content_div.find('p')
                data['excerpt'] = excerpt_p.get_text(strip=True) if excerpt_p else ''
    
            # Note: Author information is not visible in the new structure
            # If needed, it will have to be scraped from the full article page
            data['author'] = ''
            data['author_url'] = ''
    
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
            
            # Find the main article element
            article_element = soup.find('article', class_=lambda x: x and 'post' in str(x))
            
            if not article_element:
                self.log_message(run, 'warning', f'No article element found for {article_url}', article_url)
                return None
    
            article_data = {}
    
            # Extract title from h1 in entry-header
            header = article_element.find('header', class_='entry-header')
            if header:
                title_element = header.find('h1', class_='entry-title')
                article_data['full_title'] = title_element.get_text(strip=True) if title_element else ''
                
                # Extract author from entry-meta in header
                entry_meta = header.find('div', class_='entry-meta')
                if entry_meta:
                    byline = entry_meta.find('span', class_='byline')
                    if byline:
                        author_link = byline.find('a', class_='url fn n')
                        if author_link:
                            article_data['author'] = author_link.get_text(strip=True)
                            article_data['author_url'] = author_link.get('href', '')
    
            # Extract featured image from post-thumbnail div
            thumbnail_div = article_element.find('div', class_='post-thumbnail')
            if thumbnail_div:
                img_element = thumbnail_div.find('img')
                if img_element:
                    article_data['featured_image_url'] = (
                        img_element.get('src') or 
                        img_element.get('data-src') or 
                        ''
                    )
                    # Check for alt text as caption
                    article_data['image_caption'] = img_element.get('alt', '')
    
            # Extract main content
            content_div = article_element.find('div', class_='entry-content')
            full_content = []
    
            if content_div:
                # Find all paragraphs, excluding those in social sharing, ads, and related posts
                for p in content_div.find_all('p'):
                    # Skip paragraphs inside specific containers
                    if p.find_parent(class_=['wpzoom-social-sharing-buttons-top', 
                                             'google-auto-placed', 
                                             'jp-relatedposts',
                                             'wp-block-jetpack-subscriptions']):
                        continue
                    
                    # Skip empty paragraphs or those with only &nbsp;
                    text = p.get_text(strip=True)
                    if text and text != '' and len(text) > 10:
                        # Clean up whitespace
                        text = re.sub(r'\s+', ' ', text)
                        full_content.append(text)
    
            # Extract tags - they might be in footer or elsewhere
            tags = []
            footer_meta = article_element.find('footer', class_='entry-footer')
            if footer_meta:
                tag_links = footer_meta.find_all('a', rel='tag')
                tags = [tag.get_text(strip=True) for tag in tag_links]
            
            # If no tags in footer, try to find them elsewhere
            if not tags:
                # Look for any tag links in the article
                tag_links = article_element.find_all('a', rel='tag')
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
            blog_entries = soup.find('div', id='blog-entries')

            if not blog_entries:
                raise Exception("Could not find blog entries area")

            # Find all article containers
            article_containers = blog_entries.find_all('article', class_='bnm-entry')
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
