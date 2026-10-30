// Fills the download buttons and file lists from the GitHub Releases API.
// Without a network, or past the API's unauthenticated limit, the static links stay as they are.
(function () {
  var REPO = 'Orangeriger1998/ShowStack';
  var MATCH = {
    windows: function (n) { return /windows.*\.zip$/i.test(n); },
    linux: function (n) { return /linux.*\.tar\.gz$/i.test(n); }
  };
  function size(bytes) { var kb = bytes / 1024; return kb >= 1024 ? (kb / 1024).toFixed(1) + ' MB' : Math.round(kb) + ' KB'; }
  function date(iso) { try { return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' }); } catch (e) { return ''; } }
  function latestWith(releases, test) {
    for (var i = 0; i < releases.length; i++) {
      var hits = (releases[i].assets || []).filter(function (a) { return test(a.name); });
      if (hits.length) { return { release: releases[i], assets: hits, all: releases[i].assets || [] }; }
    }
    return null;
  }
  function sumFor(all, name) { for (var i = 0; i < all.length; i++) { if (all[i].name === name + '.sha256') { return all[i]; } } return null; }
  function apply(platform, found) {
    if (!found) { return; }
    Array.prototype.forEach.call(document.querySelectorAll('[data-button="' + platform + '"]'), function (b) {
      b.href = found.assets[0].browser_download_url;
      var sub = b.querySelector('.sub');
      if (sub) { sub.textContent = found.release.tag_name.replace(/^v/, '') + ' · ' + size(found.assets[0].size); }
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-platform="' + platform + '"]'), function (card) {
      var v = card.querySelector('.version'); if (v) { v.textContent = 'Version ' + found.release.tag_name.replace(/^v/, ''); }
      var d = card.querySelector('.date'); if (d) { d.textContent = 'Released ' + date(found.release.published_at); }
      var n = card.querySelector('.notes-link'); if (n) { n.href = found.release.html_url; }
      var list = card.querySelector('.files');
      if (list) {
        list.innerHTML = '';
        found.assets.forEach(function (a) {
          var li = document.createElement('li');
          var link = document.createElement('a'); link.className = 'name'; link.href = a.browser_download_url; link.textContent = a.name; li.appendChild(link);
          var s = document.createElement('span'); s.className = 'size'; s.textContent = size(a.size); li.appendChild(s);
          var sum = sumFor(found.all, a.name);
          if (sum) { var c = document.createElement('a'); c.className = 'sum'; c.href = sum.browser_download_url; c.textContent = 'SHA-256'; li.appendChild(c); }
          list.appendChild(li);
        });
      }
    });
  }
  if (!window.fetch) { return; }
  fetch('https://api.github.com/repos/' + REPO + '/releases?per_page=10', { headers: { Accept: 'application/vnd.github+json' } })
    .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.json(); })
    .then(function (releases) { apply('windows', latestWith(releases, MATCH.windows)); apply('linux', latestWith(releases, MATCH.linux)); })
    .catch(function () {});
})();
