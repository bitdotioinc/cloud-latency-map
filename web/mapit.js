var map = L.map("map").setView([2.649435, -30.97811], 3);

var currentLatency = 75;
let maxLatency = 150;
let minLatency = 10;
var currentLayerGroup;

let providerList = ["gcp", "aws"];

var dc_locations_layer = {};
var currentControls = {};
var selectedLayers = {};
var providerIcons = {};
var layerGroups = {};
var layersByWorldRegion = {};
var layersByLatency = {};

function getWorldRegion(dc_region) {
  if (
    dc_region.startsWith("us-") ||
    dc_region.startsWith("ca-") ||
    dc_region.startsWith("northamerica-")
  ) {
    return "North America";
  }
  if (dc_region.startsWith("southamerica-") || dc_region.startsWith("sa-")) {
    return "South America";
  }
  if (
    dc_region.startsWith("asia-") ||
    dc_region.startsWith("ap-") ||
    dc_region.startsWith("me-")
  ) {
    return "Asia";
  }
  if (dc_region.startsWith("europe-") || dc_region.startsWith("eu-")) {
    return "Europe";
  }
  if (dc_region.startsWith("af-")) {
    return "Africa";
  }
  if (dc_region.startsWith("australia-")) {
    return "Australia";
  }
}

for (p of providerList) {
  layersByLatency[p] = {};
  currentControls[p] = null;
  layerGroups[p] = L.layerGroup();
  providerIcons[p] = L.icon({
    iconUrl: "https://bitiocontent.com/latency/icons/" + p + ".png",
    iconSize: [20, 30],
  });
}

for (p of providerList) {
  dc_locations_layer[p] = {};
  for (let idx in dcsLocations[p].features) {
    feature = dcsLocations[p].features[idx];
    feature.properties.provider = p;
    dc_locations_layer[p][feature.properties.region] = L.geoJSON(feature, {
      pointToLayer: function (feature, latlng) {
        var circleMarker = L.marker(latlng, {
          icon: providerIcons[p],
          zIndexOffset: 1000,
        });
        return circleMarker;
      },
      onEachFeature: function (feature, layer) {
        layer.bindPopup(
          layer.feature.properties.region +
            " (" +
            p +
            ")<br/>" +
            layer.feature.properties.location
        );
      },
    }).addTo(map);
  }
}

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution:
    '&copy; <a href="http://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);

for (provider of providerList) {
  for (let idx in coverageGeoJSONs[provider].features) {
    feature = coverageGeoJSONs[provider].features[idx];
    feature.properties.provider = provider;
    latency = feature.properties.latency_ms;
    l = L.geoJSON(feature, {
      onEachFeature: onEachFeature,
      style: pickRegionColor,
    });

    l.on("remove", function (e) {
      if (
        dc_locations_layer[e.target.__provider][e.target.__name] !== "undefined"
      ) {
        map.removeLayer(
          dc_locations_layer[e.target.__provider][e.target.__name]
        );
      }
    });
    l.on("add", function (e) {
      dc_locations_layer[e.target.__provider][e.target.__name].addTo(map);
    });
    l["__name"] = feature.properties.region;
    l["__provider"] = provider;
    l["__latency"] = currentLatency;
    if (layersByLatency[provider][latency] === undefined) {
      layersByLatency[provider][latency] = {};
    }
    layersByLatency[provider][latency][feature.properties.region] = l;
  }
}

function pickRegionColor(feature, layer) {
  let region = feature.properties.region;
  return {
    color: regionToColor.get(region, "#a6cee3"),
    weight: 1,
  };
}
function onEachFeature(feature, layer) {
  if (feature.properties && feature.properties.region) {
    layer.bindPopup(
      feature.properties.provider + " - " + feature.properties.region
    );
  }
}

// create one layergroup per provider
// when changing latencies, swap the layers out

function getCurrentLayers(provider) {
  layerGroups[provider].clearLayers();
  layerGroups[provider].__removing = false;
  layerGroups[provider].__adding = true;

  for (l_name in layersByLatency[provider][currentLatency]) {
    l = layersByLatency[provider][currentLatency][l_name];
    if (!layerGroups[provider].hasLayer(l)) {
      layerGroups[provider].addLayer(l);
    }
  }
  layerGroups[provider].__adding = false;
}

var removed_layers = [];
map.on("overlayadd", function (e) {
  if (e.layer.__name !== undefined) {
    currentLayerID = generateLayerNameForControl(
      e.layer.__name,
      e.layer.__provider
    );
    if (!layerGroups[e.layer.__provider].__adding) {
      idx = removed_layers.indexOf(currentLayerID);
      if (idx != -1) {
        removed_layers.splice(idx, 1);
      }
    }
  }
});
map.on("overlayremove", function (e) {
  if (e.layer.__name !== undefined) {
    currentLayerID = generateLayerNameForControl(
      e.layer.__name,
      e.layer.__provider
    );
    if (!layerGroups[e.layer.__provider].__removing) {
      removed_layers.push(currentLayerID);
    }
  }
});

for (p of providerList) {
  getCurrentLayers(p);
  layerGroups[p].addTo(map);
}

function generateLayerNameForControl(region, provider) {
  //        float: left;
  //           margin-right: 8pxj
  return (
    '<i id="' +
    region +
    '" provider="' +
    provider +
    '" style="float: left; width: 18px; height: 18px; opacity: 0.7; background:' +
    regionToColor.get(region, "#a6cee3") +
    '"></i><span>' +
    region +
    " (" +
    provider +
    ")</span>"
  );
}
function setupMapControl(provider) {
  layers = layerGroups[provider].getLayers();
  overlays = {};
  for (let idx in layers) {
    overlays[
      generateLayerNameForControl(layers[idx].__name, layers[idx].__provider)
    ] = layers[idx];
  }

  for (let layer_name of removed_layers) {
    if (layer_name.includes('provider="' + provider)) {
      map.removeLayer(overlays[layer_name]);
    }
  }
  position = "topright";
  if (currentControls[provider] === null) {
    currentControls[provider] = L.control
      .layers(null, overlays, {
        collapsed: false,
        sortLayers: true,
        position: position,
      })
      .addTo(map);
  } else {
    for (var i = currentControls[provider]._layers.length - 1; i >= 0; i--) {
      _l = currentControls[provider]._layers[i];
      currentControls[provider].removeLayer(_l.layer);
    }
    for (var overlay_name in overlays) {
      currentControls[provider].addOverlay(
        overlays[overlay_name],
        overlay_name
      );
    }
  }
}

providersControl = L.control
  .layers(null, layerGroups, { collapsed: false, position: "topleft" })
  .addTo(map);

function updateLayersBySlider(value) {
  if (value == currentLatency || value < minLatency || value > maxLatency) {
    // do nothing
    return;
  }
  for (p of providerList) {
    layerGroups[p].__removing = true;
    currentLatency = value;
    getCurrentLayers(p);
    setupMapControl(p);
    layerGroups[p].addTo(map);
  }
}

slider = L.control
  .slider(updateLayersBySlider, {
    max: maxLatency,
    min: 10,
    value: currentLatency,
    collapsed: false,
    step: 1,
    size: "250px",
    orientation: "horizontal",
    id: "slider",
    title: "Latency (Milliseconds)",
    suffix: "ms",
  })
  .addTo(map);

for (p of providerList) {
  setupMapControl(p);
}

var credctrl = L.controlCredits({
  image: "https://bitiocontent.com/latency/icons/bitio.png",
  link: "https://bit.io/",
  width: 150,
  height: 48,
  position: "bottomleft",
}).addTo(map);
