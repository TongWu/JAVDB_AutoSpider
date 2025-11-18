import requests
from bs4 import BeautifulSoup

# 测试配置
proxy_url = "http://tedwu:No.25_Aminor@150.230.4.50:12300"
test_url = "https://javdb.com/?vft=2"

proxies = {
    'http': proxy_url,
    'https': proxy_url
}

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1'
}

print("=" * 70)
print("测试 1: 直接访问（不使用代理）")
print("=" * 70)
try:
    response = requests.get(test_url, headers=headers, timeout=10)
    print(f"状态码: {response.status_code}")
    print(f"内容长度: {len(response.text)} 字符")
    
    soup = BeautifulSoup(response.text, 'html.parser')
    movie_list = soup.find('div', class_='movie-list')
    print(f"找到 movie-list: {movie_list is not None}")
    if movie_list:
        items = movie_list.find_all('div', class_='item')
        print(f"找到 {len(items)} 个电影条目")
except Exception as e:
    print(f"错误: {e}")

print("\n" + "=" * 70)
print("测试 2: 使用代理访问")
print("=" * 70)
try:
    response = requests.get(test_url, headers=headers, proxies=proxies, timeout=30)
    print(f"状态码: {response.status_code}")
    print(f"内容长度: {len(response.text)} 字符")
    
    soup = BeautifulSoup(response.text, 'html.parser')
    movie_list = soup.find('div', class_='movie-list')
    print(f"找到 movie-list: {movie_list is not None}")
    if movie_list:
        items = movie_list.find_all('div', class_='item')
        print(f"找到 {len(items)} 个电影条目")
    
    # 显示前 500 个字符看看返回了什么
    print("\n前 500 个字符:")
    print(response.text[:500])
except Exception as e:
    print(f"错误: {e}")

print("\n" + "=" * 70)
print("测试完成")
print("=" * 70)
