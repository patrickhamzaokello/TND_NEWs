# TND News Uganda Custom Web Scraper for Google Colab
# Website: https://tndnewsuganda.com/news/

# Install required packages

import requests
from bs4 import BeautifulSoup
import pandas as pd
import json
import time
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
import os


class TNDNewsScraper:
    def __init__(self):
        self.base_url = "https://tndnewsuganda.com"
        self.news_url = "https://tndnewsuganda.com"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def extract_article_data(self, article_div):
        """
        Extract data from a single article div element
        """
        try:
            data = {}

            # Extract post ID and classes for metadata
            post_div = article_div.find('div', class_=lambda x: x and 'post-' in x)
            if post_div:
                classes = post_div.get('class', [])
                post_classes = [cls for cls in classes if cls.startswith('post-')]
                data['post_id'] = post_classes[0] if post_classes else ''

                # Extract categories and tags from classes
                categories = [cls.replace('category-', '') for cls in classes if cls.startswith('category-')]
                tags = [cls.replace('tag-', '') for cls in classes if cls.startswith('tag-')]
                data['categories'] = categories
                data['tags'] = tags

            # Extract title and URL
            title_element = article_div.find('h2', class_='entry-title')
            if title_element:
                title_link = title_element.find('a')
                if title_link:
                    data['title'] = title_link.get_text(strip=True)
                    data['url'] = title_link.get('href', '')
                else:
                    data['title'] = title_element.get_text(strip=True)
                    data['url'] = ''
            else:
                data['title'] = ''
                data['url'] = ''

            # Extract featured image
            img_element = article_div.find('a', class_='post-img')
            if img_element:
                style = img_element.get('style', '')
                # Extract URL from background-image style
                img_match = re.search(r"url\('([^']+)'\)", style)
                data['featured_image'] = img_match.group(1) if img_match else ''
            else:
                data['featured_image'] = ''

            # Extract category from category-meta
            category_element = article_div.find('div', class_='cat-links')
            if category_element:
                category_link = category_element.find('a')
                data['primary_category'] = category_link.get_text(strip=True) if category_link else ''
            else:
                data['primary_category'] = ''

            # Extract publication date
            date_element = article_div.find('div', class_='date')
            if date_element:
                date_link = date_element.find('a')
                data['published_time'] = date_link.get_text(strip=True) if date_link else ''
            else:
                data['published_time'] = ''

            # Extract author
            author_element = article_div.find('div', class_='by-author')
            if author_element:
                author_link = author_element.find('a')
                data['author'] = author_link.get_text(strip=True) if author_link else ''
            else:
                data['author'] = ''

            # Extract excerpt/summary
            content_element = article_div.find('div', class_='entry-content')
            if content_element:
                excerpt_p = content_element.find('p')
                data['excerpt'] = excerpt_p.get_text(strip=True) if excerpt_p else ''
            else:
                data['excerpt'] = ''

            # Add scraping metadata
            data['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            data['source_website'] = 'TND News Uganda'

            return data

        except Exception as e:
            print(f"Error extracting article data: {e}")
            return None

    def scrape_news_page(self, page_url=None):
        """
        Scrape all news articles from a page
        """
        if page_url is None:
            page_url = self.news_url

        try:
            print(f"Scraping: {page_url}")
            response = self.session.get(page_url)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # Find the main content area
            main_element = soup.find('main', id='main')
            if not main_element:
                print("Could not find main content area")
                return []

            # Find all article containers
            article_containers = main_element.find_all('div', class_='col-sm-6 col-xxl-4 post-col')
            print(f"Found {len(article_containers)} articles")

            articles_data = []
            for i, container in enumerate(article_containers):
                print(f"Processing article {i + 1}/{len(article_containers)}...")
                article_data = self.extract_article_data(container)
                if article_data:
                    articles_data.append(article_data)

                # Small delay to be respectful
                time.sleep(0.5)

            return articles_data

        except Exception as e:
            print(f"Error scraping page {page_url}: {e}")
            return []

    def scrape_full_article_content(self, article_url):
        """
        Scrape the full content of an individual article based on TND News structure
        """
        try:
            response = self.session.get(article_url)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # Find the main content area
            main_element = soup.find('main', id='main')
            if not main_element:
                print(f"Could not find main content for {article_url}")
                return None

            article_data = {}

            # Extract article title (h1)
            title_element = main_element.find('h1', class_='entry-title')
            article_data['full_title'] = title_element.get_text(strip=True) if title_element else ''

            # Extract featured image and caption
            featured_img = main_element.find('figure', class_='post-featured-image')
            if featured_img:
                img_div = featured_img.find('div', class_='post-img')
                if img_div:
                    style = img_div.get('style', '')
                    img_match = re.search(r"url\('([^']+)'\)", style)
                    article_data['featured_image_url'] = img_match.group(1) if img_match else ''

                # Extract image caption
                caption = featured_img.find('figcaption', class_='featured-image-caption')
                article_data['image_caption'] = caption.get_text(strip=True) if caption else ''
            else:
                article_data['featured_image_url'] = ''
                article_data['image_caption'] = ''

            # Extract category from category-meta
            category_meta = main_element.find('div', class_='entry-meta category-meta')
            if category_meta:
                cat_link = category_meta.find('a')
                article_data['category'] = cat_link.get_text(strip=True) if cat_link else ''
            else:
                article_data['category'] = ''

            # Extract publication date and author from entry-meta
            entry_meta = main_element.find('header', class_='entry-header').find('div', class_='entry-meta')
            if entry_meta:
                # Date
                date_div = entry_meta.find('div', class_='date')
                if date_div:
                    date_link = date_div.find('a')
                    article_data['published_time'] = date_link.get_text(strip=True) if date_link else ''

                # Author
                author_div = entry_meta.find('div', class_='by-author')
                if author_div:
                    author_link = author_div.find('a')
                    article_data['author'] = author_link.get_text(strip=True) if author_link else ''

            # Extract main article content
            content_div = main_element.find('div', class_='entry-content')
            full_content = []

            if content_div:
                # Get all paragraphs, excluding social buttons and other non-content
                paragraphs = content_div.find_all('p')
                for p in paragraphs:
                    # Skip paragraphs that are part of social sharing or other UI elements
                    if not p.find_parent(class_='simplesocialbuttons'):
                        text = p.get_text(strip=True)
                        if len(text) > 10:  # Filter out very short paragraphs
                            # Clean up the text (remove excessive whitespace)
                            text = re.sub(r'\s+', ' ', text)
                            full_content.append(text)

            # Extract tags from footer
            footer_meta = main_element.find('footer', class_='entry-meta')
            tags = []
            if footer_meta:
                tag_links = footer_meta.find_all('a', rel='tag')
                tags = [tag.get_text(strip=True) for tag in tag_links]

            article_data.update({
                'full_content': '\n\n'.join(full_content),
                'word_count': len(' '.join(full_content).split()),
                'paragraph_count': len(full_content),
                'tags': tags,
                'content_paragraphs': full_content  # Keep individual paragraphs
            })

            return article_data

        except Exception as e:
            print(f"Error scraping full content from {article_url}: {e}")
            return None

    def scrape_with_full_content(self, max_articles=10):
        """
        Scrape articles with full content (slower but more complete)
        """
        print("Scraping articles with full content...")

        # First get the basic article data
        articles = self.scrape_news_page()

        if not articles:
            print("No articles found")
            return []

        # Limit the number of articles to process
        articles = articles[:max_articles]

        # Get full content for each article
        for i, article in enumerate(articles):
            if article['url']:
                print(f"Getting full content for article {i + 1}/{len(articles)}: {article['title'][:50]}...")
                full_content_data = self.scrape_full_article_content(article['url'])
                if full_content_data:
                    article.update(full_content_data)

                # Respectful delay
                time.sleep(1)

        return articles

    def save_to_json(self, data, filename='tnd_news_data.json'):
        """
        Save scraped data to JSON file
        """
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"Data saved to {filename}")
            return True
        except Exception as e:
            print(f"Error saving JSON: {e}")
            return False

    def save_to_csv(self, data, filename='tnd_news_data.csv'):
        """
        Save scraped data to CSV file
        """
        try:
            df = pd.DataFrame(data)

            # Convert list columns to strings for CSV compatibility
            for col in ['categories', 'tags']:
                if col in df.columns:
                    df[col] = df[col].apply(lambda x: ', '.join(x) if isinstance(x, list) else str(x))

            df.to_csv(filename, index=False, encoding='utf-8')
            print(f"Data saved to {filename}")
            return df
        except Exception as e:
            print(f"Error saving CSV: {e}")
            return None

    def display_summary(self, data):
        """
        Display summary of scraped data
        """
        if not data:
            print("No data to display")
            return

        print(f"\n{'=' * 60}")
        print(f"TND NEWS UGANDA - SCRAPING SUMMARY")
        print(f"{'=' * 60}")
        print(f"Total articles scraped: {len(data)}")

        # Category analysis
        all_categories = []
        for article in data:
            category = article.get('primary_category') or article.get('category', '')
            if category:
                all_categories.append(category)

        if all_categories:
            category_counts = pd.Series(all_categories).value_counts()
            print(f"\nCategories found:")
            for cat, count in category_counts.head().items():
                print(f"  - {cat}: {count} articles")

        # Tags analysis
        all_tags = []
        for article in data:
            if 'tags' in article and isinstance(article['tags'], list):
                all_tags.extend(article['tags'])

        if all_tags:
            tag_counts = pd.Series(all_tags).value_counts()
            print(f"\nMost common tags:")
            for tag, count in tag_counts.head(10).items():
                print(f"  - {tag}: {count} articles")

        # Word count analysis (if available)
        word_counts = [article.get('word_count', 0) for article in data if 'word_count' in article]
        if word_counts:
            print(f"\nContent analysis:")
            print(f"  - Average words per article: {sum(word_counts) / len(word_counts):.1f}")
            print(f"  - Total words scraped: {sum(word_counts)}")
            print(f"  - Longest article: {max(word_counts)} words")
            print(f"  - Shortest article: {min(word_counts)} words")

        print(f"\n{'=' * 60}")
        print(f"SAMPLE ARTICLES")
        print(f"{'=' * 60}")

        for i, article in enumerate(data[:3]):
            print(f"\n{i + 1}. {article.get('title', article.get('full_title', 'No title'))}")
            print(f"   Category: {article.get('primary_category', article.get('category', 'N/A'))}")
            print(f"   Author: {article.get('author', 'N/A')}")
            print(f"   Time: {article.get('published_time', 'N/A')}")

            if article.get('tags'):
                print(f"   Tags: {', '.join(article['tags'][:3])}{'...' if len(article['tags']) > 3 else ''}")

            if article.get('image_caption'):
                print(f"   Image Caption: {article['image_caption'][:80]}...")

            if article.get('excerpt'):
                print(f"   Excerpt: {article['excerpt'][:100]}...")
            elif article.get('full_content'):
                # Show first sentence of full content if no excerpt
                first_sentence = article['full_content'].split('.')[0]
                print(f"   Content Preview: {first_sentence[:100]}...")

            print(f"   URL: {article['url']}")

            if article.get('word_count'):
                print(f"   Word Count: {article['word_count']} words")


# Main execution functions
def scrape_headlines_only():
    """
    Quick scraping - just headlines and basic info
    """
    print("=== QUICK SCRAPE: Headlines Only ===")
    scraper = TNDNewsScraper()

    articles = scraper.scrape_news_page()

    if articles:
        # Save data
        scraper.save_to_json(articles, 'tnd_headlines.json')
        df = scraper.save_to_csv(articles, 'tnd_headlines.csv')
        scraper.display_summary(articles)

        return articles, df
    else:
        print("No articles found")
        return [], None


def scrape_full_articles(max_articles=10):
    """
    Complete scraping - headlines + full article content
    """
    print(f"=== FULL SCRAPE: {max_articles} Articles with Full Content ===")
    scraper = TNDNewsScraper()

    articles = scraper.scrape_with_full_content(max_articles)

    if articles:
        # Save data
        scraper.save_to_json(articles, 'tnd_full_articles.json')
        df = scraper.save_to_csv(articles, 'tnd_full_articles.csv')
        scraper.display_summary(articles)

        return articles, df
    else:
        print("No articles found")
        return [], None


def search_articles_by_category(category_filter=None):
    """
    Scrape and filter articles by category
    """
    scraper = TNDNewsScraper()
    articles = scraper.scrape_news_page()

    if category_filter:
        filtered_articles = [
            article for article in articles
            if category_filter.lower() in article.get('primary_category', '').lower()
        ]
        print(f"Found {len(filtered_articles)} articles in category '{category_filter}'")
        return filtered_articles

    return articles


def analyze_news_data(data):
    """
    Analyze the scraped news data with enhanced analysis for TND News
    """
    if not data:
        print("No data to analyze")
        return

    df = pd.DataFrame(data)

    print(f"\n{'=' * 50}")
    print(f"TND NEWS DATA ANALYSIS")
    print(f"{'=' * 50}")

    # Basic statistics
    print(f"Total articles: {len(df)}")

    # Category analysis
    category_col = 'primary_category' if 'primary_category' in df.columns else 'category'
    if category_col in df.columns:
        print(f"\nCategory distribution:")
        category_counts = df[category_col].value_counts()
        for cat, count in category_counts.items():
            percentage = (count / len(df)) * 100
            print(f"  {cat}: {count} articles ({percentage:.1f}%)")

    # Author analysis
    if 'author' in df.columns:
        print(f"\nTop authors:")
        author_counts = df['author'].value_counts().head()
        for author, count in author_counts.items():
            print(f"  {author}: {count} articles")

    # Tags analysis (enhanced for TND News)
    if 'tags' in df.columns:
        all_tags = []
        for tags in df['tags']:
            if isinstance(tags, list):
                all_tags.extend(tags)
            elif isinstance(tags, str) and tags:
                # Handle CSV format where tags are comma-separated
                all_tags.extend([tag.strip() for tag in tags.split(',')])

        if all_tags:
            tag_counts = pd.Series(all_tags).value_counts()
            print(f"\nMost popular tags:")
            for tag, count in tag_counts.head(10).items():
                print(f"  {tag}: {count} articles")

    # Time analysis
    if 'published_time' in df.columns:
        print(f"\nRecent publication times:")
        time_counts = df['published_time'].value_counts().head()
        for time_val, count in time_counts.items():
            print(f"  {time_val}: {count} articles")

    # Content analysis (if full content was scraped)
    if 'word_count' in df.columns:
        word_counts = df['word_count'][df['word_count'] > 0]
        if len(word_counts) > 0:
            print(f"\nContent statistics:")
            print(f"  Average words per article: {word_counts.mean():.1f}")
            print(f"  Longest article: {word_counts.max()} words")
            print(f"  Shortest article: {word_counts.min()} words")
            print(f"  Total words scraped: {word_counts.sum()}")

    # Image analysis
    if 'featured_image_url' in df.columns:
        articles_with_images = df['featured_image_url'].notna().sum()
        print(f"\nMedia statistics:")
        print(
            f"  Articles with featured images: {articles_with_images}/{len(df)} ({(articles_with_images / len(df) * 100):.1f}%)")

    # Title keyword analysis
    if 'title' in df.columns:
        title_col = 'title'
    elif 'full_title' in df.columns:
        title_col = 'full_title'
    else:
        title_col = None

    if title_col:
        all_titles = ' '.join(df[title_col].astype(str).str.lower())
        words = re.findall(r'\b\w+\b', all_titles)
        # Filter out common words
        stop_words = {'the', 'and', 'for', 'are', 'with', 'his', 'her', 'this', 'that', 'from', 'they', 'been', 'have',
                      'has', 'will', 'said', 'says'}
        words = [w for w in words if w not in stop_words and len(w) > 3]
        word_freq = pd.Series(words).value_counts()

        print(f"\nMost common keywords in headlines:")
        for word, freq in word_freq.head(10).items():
            print(f"  {word}: {freq} times")


# Example usage functions
def quick_demo():
    """
    Quick demonstration of the scraper
    """
    print("TND News Uganda Scraper Demo")
    print("Website: https://tndnewsuganda.com/")
    print("=" * 60)

    # Quick scrape
    articles, df = scrape_headlines_only()

    if articles:
        print(f"\nSuccessfully scraped {len(articles)} articles!")
        print("\nFiles created:")
        print("- tnd_headlines.json")
        print("- tnd_headlines.csv")

    return articles, df


def full_demo(max_articles=5):
    """
    Full demonstration with article content using the actual TND News structure
    """
    print("TND News Uganda Full Content Scraper Demo")
    print("Website: https://tndnewsuganda.com/")
    print("=" * 60)

    # Full scrape
    articles, df = scrape_full_articles(max_articles)

    if articles:
        print(f"\nSuccessfully scraped {len(articles)} full articles!")
        print("\nFiles created:")
        print("- tnd_full_articles.json")
        print("- tnd_full_articles.csv")

        # Show sample of extracted data
        print(f"\n{'=' * 40}")
        print("SAMPLE EXTRACTED DATA:")
        print(f"{'=' * 40}")

        if articles:
            sample = articles[0]
            print(f"Title: {sample.get('title', sample.get('full_title', 'N/A'))}")
            print(f"Category: {sample.get('category', sample.get('primary_category', 'N/A'))}")
            print(f"Author: {sample.get('author', 'N/A')}")
            print(f"Published: {sample.get('published_time', 'N/A')}")

            if sample.get('tags'):
                print(f"Tags: {', '.join(sample['tags'])}")

            if sample.get('image_caption'):
                print(f"Image Caption: {sample['image_caption']}")

            if sample.get('full_content'):
                content_preview = sample['full_content'][:200] + "..." if len(sample['full_content']) > 200 else sample[
                    'full_content']
                print(f"Content Preview: {content_preview}")
                print(f"Word Count: {sample.get('word_count', 'N/A')}")

        # Analyze the data
        analyze_news_data(articles)

    return articles, df


# Utility functions
def filter_by_keyword(data, keyword):
    """
    Filter articles by keyword in title or content
    """
    filtered = []
    keyword_lower = keyword.lower()

    for article in data:
        title_match = keyword_lower in article.get('title', '').lower()
        excerpt_match = keyword_lower in article.get('excerpt', '').lower()
        content_match = keyword_lower in article.get('full_content', '').lower()

        if title_match or excerpt_match or content_match:
            filtered.append(article)

    return filtered


def export_filtered_data(data, keyword, output_prefix='filtered'):
    """
    Export filtered data to JSON and CSV
    """
    if not data:
        print("No data to export")
        return

    # Create filenames
    json_filename = f"{output_prefix}_{keyword.replace(' ', '_')}.json"
    csv_filename = f"{output_prefix}_{keyword.replace(' ', '_')}.csv"

    # Save JSON
    with open(json_filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Save CSV
    df = pd.DataFrame(data)
    for col in ['categories', 'tags']:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: ', '.join(x) if isinstance(x, list) else str(x))

    df.to_csv(csv_filename, index=False, encoding='utf-8')

    print(f"Filtered data saved:")
    print(f"- {json_filename}")
    print(f"- {csv_filename}")

    return df


# Main execution
print("TND News Uganda Custom Scraper Ready!")
print("=" * 50)
print("Website: https://tndnewsuganda.com/")
print("Designed for TND News Uganda's specific HTML structure")
print("=" * 50)
print("Available functions:")
print("1. quick_demo() - Fast scraping of headlines only")
print("2. full_demo(max_articles=5) - Full articles with complete content")
print("3. search_articles_by_category('politics') - Filter by category")
print("4. filter_by_keyword(data, 'election') - Search by keyword")
print("5. scraper.scrape_full_article_content(url) - Scrape individual article")
print("=" * 50)

# Test the scraper with a sample article first
print("\n🧪 Testing individual article scraping...")
scraper = TNDNewsScraper()

# Test with the specific article structure you provided
test_url = "https://tndnewsuganda.com/2025/08/18/dokolo-dr-lalam-turns-blue-ahead-of-2026-polls/"
print(f"Testing with: {test_url}")

test_article = scraper.scrape_full_article_content(test_url)
if test_article:
    print("✅ Individual article scraping test successful!")
    print(f"   Title: {test_article.get('full_title', 'N/A')}")
    print(f"   Category: {test_article.get('category', 'N/A')}")
    print(f"   Author: {test_article.get('author', 'N/A')}")
    print(f"   Tags: {', '.join(test_article.get('tags', []))}")
    print(f"   Word count: {test_article.get('word_count', 'N/A')}")
    if test_article.get('image_caption'):
        print(f"   Image caption: {test_article['image_caption']}")
else:
    print("❌ Test failed - check the URL or HTML structure")

print("\n" + "=" * 50)
print("Now running full demo...")
articles_data, df = quick_demo()

# Display usage examples
print(f"\n{'=' * 60}")
print("USAGE EXAMPLES:")
print(f"{'=' * 60}")
print("""
# Quick scraping (headlines only):
articles, df = quick_demo()

# Full content scraping (slower):
full_articles, full_df = full_demo(max_articles=8)

# Search for specific topics:
politics_articles = search_articles_by_category('politics')
election_articles = filter_by_keyword(articles_data, 'election')

# Export filtered data:
filtered_df = export_filtered_data(election_articles, 'election', 'politics_news')

# Analyze data:
analyze_news_data(articles_data)
""")

print(f"\nData structure for each article:")
if articles_data:
    sample_keys = list(articles_data[0].keys())
    for key in sample_keys:
        print(f"  - {key}")

print(f"\nFiles generated in this session:")
print(f"  - tnd_headlines.json")
print(f"  - tnd_headlines.csv")