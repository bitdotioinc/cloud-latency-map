// a simple control to display a logo and credits in the corner of the map, with some neat interactive behavior
// in Leaflet tradition, a shortcut method is also provided, so you may use either version:
//     new L.CreditsControl(options)
//     L.controlCredits(options)
L.controlCredits = function (options) {
    return new L.CreditsControl(options);
}

L.CreditsControl = L.Control.extend({
    options: {
        position: 'bottomright'
    },
    initialize: function(options) {
        if (! options.image) throw "L.CreditsControl missing required option: image";
        if (! options.link)  throw "L.CreditsControl missing required option: link";

        L.setOptions(this,options);
    },
    onAdd: function (map) {
        this._map = map;

        // create our link, and set the background image
        var link = L.DomUtil.create('a', 'leaflet-credits-control', link);
        link.style.backgroundImage = 'url(' + this.options.image + ')';
        link.tabIndex = 0;
        if (this.options.width)  link.style.width = this.options.width + 'px';
        if (this.options.height) link.style.height = this.options.height + 'px';

        // generate the hyperlink to the left-hand side
        link.target     = '_blank';
        link.href       = this.options.link;



        L.DomEvent.addListener(link, 'keydown', function(event) {
            if (event.key == 'Enter') link.click();
        });

        // keep mouse events from falling through to the map: don't drag-pan or double-click the map on accident
//        L.DomEvent.disableClickPropagation(link);
        L.DomEvent.disableScrollPropagation(link);

        // keep a reference to our link and to the link
        this._link = link;

        // all done
        return link;
    },
    setText: function (html) {
        this._link.innerHTML = html;
    }
});
