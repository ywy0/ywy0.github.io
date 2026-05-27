---
layout: page
title: 归档
permalink: /archive/
---

共 {{ site.posts.size }} 篇

{% for post in site.posts %}
{% assign year = post.date | date: "%Y" %}
{% if year != prev_year %}
## {{ year }}
{% assign prev_year = year %}
{% endif %}
- _{{ post.date | date: "%Y-%m-%d" }}_ [{{ post.title }}]({{ post.url }})
{% endfor %}
