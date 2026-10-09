(function () {
  var tab = new URLSearchParams(window.location.search).get("tab") || window.location.hash.replace(/^#tab-/, "");
  if (["managers", "joins", "projects"].indexOf(tab) !== -1) {
    window.location.replace("/colab/admin?tab=" + encodeURIComponent(tab));
  }
}());
