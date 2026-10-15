import L from 'leaflet'

// Leaflet 1.9 ends an animated zoom from a 250 ms timer that `map.remove()` does not
// clear. When a page goes away mid-zoom (a parent taps a school while the search map is
// still zooming to it), the timer runs on the removed map and throws "Cannot read
// properties of undefined (reading '_leaflet_pos')". A removed map has no map pane, so
// the late call is dropped. Imported for its effect by every module that draws a map.
const endZoom = L.Map.prototype._onZoomTransitionEnd
if (!endZoom.__guarded) {
  L.Map.prototype._onZoomTransitionEnd = function guardedZoomEnd(...args) {
    if (!this._mapPane) {
      this._animatingZoom = false
      return undefined
    }
    return endZoom.apply(this, args)
  }
  L.Map.prototype._onZoomTransitionEnd.__guarded = true
}
