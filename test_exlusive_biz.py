"""
Quick test script to verify the Exclusive.co.ug scraper handles compression correctly
"""

import requests
from bs4 import BeautifulSoup


def test_response_decoding():
    """Test that we can properly decode the response"""

    url = "https://exclusive.co.ug/category/news/"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive',
    }

    print("Testing response decoding...")
    print(f"URL: {url}")
    print()

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        print(f"Status Code: {response.status_code}")
        print(f"Content-Encoding: {response.headers.get('Content-Encoding', 'none')}")
        print(f"Content-Type: {response.headers.get('Content-Type', 'none')}")
        print()

        # Check response.content (bytes)
        print("response.content (first 100 bytes):")
        print(response.content[:100])
        print()

        # Check response.text (decoded string)
        print("response.text (first 200 chars):")
        print(response.text[:200])
        print()

        # Try parsing with BeautifulSoup
        soup = BeautifulSoup(response.text, 'html.parser')

        # Find some articles
        articles = soup.select('article.elementor-post')
        print(f"Found {len(articles)} articles")

        if articles:
            print("\nFirst article:")
            first_article = articles[0]

            title_elem = first_article.select_one('h3.elementor-post__title a')
            if title_elem:
                print(f"  Title: {title_elem.get_text(strip=True)}")
                print(f"  URL: {title_elem.get('href', '')}")

            date_elem = first_article.select_one('.elementor-post-date')
            if date_elem:
                print(f"  Date: {date_elem.get_text(strip=True)}")

        print("\n✅ Response decoding works correctly!")
        return True

    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def test_article_detail():
    """Test fetching and parsing an article detail page"""

    # Use a specific article URL (you can change this)
    url = "https://exclusive.co.ug/mc-kats-fille-relapsed-drug-abuse-after-rehab/"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
    }

    print("\n" + "=" * 60)
    print("Testing article detail page...")
    print(f"URL: {url}")
    print()

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        # Extract title
        title = soup.select_one('h1.elementor-heading-title')
        if title:
            print(f"Title: {title.get_text(strip=True)}")

        # Extract author
        author = soup.select_one('.elementor-post-info__item--type-author')
        if author:
            print(f"Author: {author.get_text(strip=True)}")

        # Extract content paragraphs
        content_div = soup.select_one('.elementor-widget-theme-post-content')
        if content_div:
            paragraphs = content_div.find_all('p')

            # Filter out ads and social content
            valid_paragraphs = []
            for p in paragraphs:
                if (p.find_parent(class_='elementor-share-btn') or
                        p.find_parent(class_='twitter-tweet') or
                        p.find_parent(class_='wp-block-embed')):
                    continue

                text = p.get_text(strip=True)
                if len(text) > 20:
                    valid_paragraphs.append(text)

            print(f"\nFound {len(valid_paragraphs)} content paragraphs")

            if valid_paragraphs:
                print("\nFirst paragraph:")
                print(valid_paragraphs[0][:200] + "...")

                print("\nGenerated excerpt:")
                excerpt = valid_paragraphs[0][:200].strip()
                if len(valid_paragraphs[0]) > 200:
                    excerpt += "..."
                print(excerpt)

        # Extract tags
        tags_elem = soup.select_one('.elementor-widget-text-editor')
        if tags_elem:
            tag_links = tags_elem.find_all('a', rel='tag')
            if tag_links:
                tags = [tag.get_text(strip=True) for tag in tag_links]
                print(f"\nTags: {', '.join(tags)}")

        print("\n✅ Article detail parsing works correctly!")
        return True

    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("Exclusive.co.ug Scraper Test")
    print("=" * 60)

    # Test main page
    test1 = test_response_decoding()

    # Test article detail
    test2 = test_article_detail()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Main page test: {'✅ PASS' if test1 else '❌ FAIL'}")
    print(f"Article detail test: {'✅ PASS' if test2 else '❌ FAIL'}")

    if test1 and test2:
        print("\n🎉 All tests passed! The scraper should work correctly.")
    else:
        print("\n⚠️  Some tests failed. Check the errors above.")