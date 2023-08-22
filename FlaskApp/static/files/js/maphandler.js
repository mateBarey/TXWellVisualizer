function onEachFeature(feature, layer) {
  layer.on({
    click: function (e) {
      var fid = feature.properties.FID;
      var district = feature.properties.district;
      var url = "/map2?FID=" + fid + "&district=" + district;
      window.location.href = url;
    }
  });
}