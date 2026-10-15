import L from 'leaflet'

// Leaflet 1.9 ends an animated zoom from a 250 ms timer that `map.remove()` does not
// clear. When a page goes away mid-zoom (a parent taps a school while the search map is
// still zooming to it), the timer runs on the removed map and throws "Cannot read
// properties of undefined (reading '_leaflet_pos')". A removed map has no map pane, so
// the late call is dropped. Imported for its effect by every module that draws a map.
// A private method: if a Leaflet upgrade renames it, leave Leaflet as it is rather than
// break every map on import.
const endZoom = L.Map.prototype._onZoomTransitionEnd
if (typeof endZoom === 'function' && !endZoom.__guarded) {
  L.Map.prototype._onZoomTransitionEnd = function guardedZoomEnd(...args) {
    if (!this._mapPane) {
      this._animatingZoom = false
      delete this._tempFireZoomEvent
      return undefined
    }
    return endZoom.apply(this, args)
  }
  L.Map.prototype._onZoomTransitionEnd.__guarded = true
}
