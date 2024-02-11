import time
from datetime import datetime, timedelta

import feedparser
import requests
import re


# Function to print the updated feed object if there is a new item
def print_updated_feed(feed_url, last_item_date):
    feed = feedparser.parse(feed_url)
    if feed.entries:
        entries = [entry for entry in feed.entries if entry.published_parsed > last_item_date]
        for entry in entries:
            print(entry.title)
            link = entry.link
            directory = '/tmp/torrent'

            pattern = r'\bUFC\b.*?1080p'

            # Search for the pattern
            match = re.search(pattern, entry.title)

            if match:
                response = requests.get(link, stream=True)
                if response.status_code == 200:
                    print(f"Gotten torrent for {entry.title}")
                    filename = entry.title.replace(" ", ".")

                    # Open a local file with wb (write binary) permission
                    with open(f"{directory}/{filename}.torrent", 'wb') as f:
                        # Write the content of the response to the file
                        f.write(response.content)

                else:
                    print("Failed to retrieve the file.")

        return feed.entries[0].published_parsed  # return the latest
    return last_item_date


# RSS feed URL
feed_url = "https://ipt.beelyrics.net/t.rss?u=1055325;tp=0eab5d9e862bcc93ea13eef9ae846b13;55;download"

# Variable to hold the date of the last item printed
now = datetime.now()

# Start a week ago as the torrent is most likely still there if we get restarted.
one_hour_ago = now - timedelta(days=7)

# Convert the datetime object to a struct_time object
last_item_date = one_hour_ago.timetuple()

# Main loop to poll the feed every minute
while True:
    last_item_date = print_updated_feed(feed_url, last_item_date)
    time.sleep(60)  # Sleep for  60 seconds (1 minute)
