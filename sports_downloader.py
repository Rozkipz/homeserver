import requests
from bs4 import BeautifulSoup

top_level_url = "https://www.sport-video.org.ua/"
hockey_url = f"{top_level_url}/hockey1.html"
urls = {f"{top_level_url}/other.html": ["ufc", "cricket"], f"{top_level_url}/hockey1.html": ["montreal"]}

directory = '/tmp/torrent'
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT  10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36'}

for url, check_strings in urls.items():
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')

        body = soup.find('body')
        divs = body.find_all('div')

        for div in divs:
            if div.has_attr('id') and "wb_LayoutGrid" in div['id'] and "#FF0000" in str(div):
                game_name = div.find('span').find("strong")
                if any(substring in game_name.text.lower() for substring in check_strings):
                    escaped_name = game_name.text.strip().replace(" ", "%20")
                    link = f"{top_level_url}/{escaped_name}.mkv.torrent"

                    response = requests.get(link, stream=True, headers=headers)
                    if response.status_code == 200:
                        print(f"Gotten torrent for {game_name.text}")
                        filename = game_name.text.replace(" ", ".")

                        # Open a local file with wb (write binary) permission
                        with open(f"{directory}/{filename}.torrent", 'wb') as f:
                            # Write the content of the response to the file
                            f.write(response.content)

                    print(f"{link}")

    else:
        print("Failed to retrieve the webpage")
