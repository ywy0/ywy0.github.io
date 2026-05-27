---
layout: home
---

<script>
(function() {
  var poems = [
    {% for p in site.tags.poem %}
    "{{ p.url }}"{% unless forloop.last %},{% endunless %}
    {% endfor %}
  ];
  if (poems.length > 0) {
    var chosen = poems[Math.floor(Math.random() * poems.length)];
    window.location.replace(chosen);
  }
})();
</script>

<noscript>
  <p>共 {{ site.tags.poem | size }} 篇诗文。</p>
  <p><a href="/archive/">查看归档</a></p>
</noscript>
