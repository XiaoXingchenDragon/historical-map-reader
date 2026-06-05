'use client'

import L, { LatLngExpression, LayerGroup, Map as LeafletMap, Marker } from 'leaflet'
import { useEffect, useMemo, useRef } from 'react'
import { Mention } from '@/lib/api'

type Props = {
  mentions: Mention[]
  selectedMentionId: number | null
  fitKey: number | null
  onMarkerClick: (mention: Mention) => void
}

const mapConfig = {
  defaultCenter: [35, 20] as LatLngExpression,
  defaultZoom: 3,
  tileUrl: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  tileAttribution: '&copy; OpenStreetMap contributors'
}

export default function MapPanel({ mentions, selectedMentionId, fitKey, onMarkerClick }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<LeafletMap | null>(null)
  const layerRef = useRef<LayerGroup | null>(null)
  const markersRef = useRef<globalThis.Map<number, Marker>>(new globalThis.Map<number, Marker>())
  const didFitBoundsRef = useRef(false)

  const mappedMentions = useMemo(
    () => mentions.filter((mention) => typeof mention.lat === 'number' && typeof mention.lng === 'number'),
    [mentions]
  )

  useEffect(() => {
    didFitBoundsRef.current = false
  }, [fitKey])

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    const map = L.map(containerRef.current).setView(mapConfig.defaultCenter, mapConfig.defaultZoom)
    L.tileLayer(mapConfig.tileUrl, {
      attribution: mapConfig.tileAttribution,
      maxZoom: 18
    }).addTo(map)
    mapRef.current = map
    layerRef.current = L.layerGroup().addTo(map)

    return () => {
      map.remove()
      mapRef.current = null
      layerRef.current = null
      markersRef.current.clear()
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    const layer = layerRef.current
    if (!map || !layer) return

    const nextIds = new Set(mappedMentions.map((mention) => mention.id))
    markersRef.current.forEach((marker, id) => {
      if (!nextIds.has(id)) {
        layer.removeLayer(marker)
        markersRef.current.delete(id)
      }
    })

    mappedMentions.forEach((mention) => {
      const latLng: LatLngExpression = [mention.lat as number, mention.lng as number]
      const label = mention.canonical_name || mention.raw_name
      const existing = markersRef.current.get(mention.id)
      if (existing) {
        existing.setLatLng(latLng)
        existing.unbindTooltip()
        existing.bindTooltip(label)
        existing.off('click')
        existing.on('click', () => onMarkerClick(mention))
        return
      }

      const marker = L.marker(latLng, {
        icon: L.divIcon({
          className: '',
          html: `<div class="marker-dot">${mention.raw_name.slice(0, 1)}</div>`,
          iconSize: [28, 28],
          iconAnchor: [14, 14]
        })
      })
      marker.bindTooltip(label)
      marker.on('click', () => onMarkerClick(mention))
      marker.addTo(layer)
      markersRef.current.set(mention.id, marker)
    })

    if (mappedMentions.length > 0 && !didFitBoundsRef.current) {
      const bounds = L.latLngBounds(mappedMentions.map((item) => [item.lat as number, item.lng as number]))
      map.fitBounds(bounds.pad(0.18), { maxZoom: 6 })
      didFitBoundsRef.current = true
    }
  }, [mappedMentions, onMarkerClick])

  useEffect(() => {
    if (selectedMentionId === null) return
    const map = mapRef.current
    const marker = markersRef.current.get(selectedMentionId)
    if (!map || !marker) return
    map.flyTo(marker.getLatLng(), Math.max(map.getZoom(), 7), { duration: 0.7 })
    marker.openTooltip()
  }, [selectedMentionId])

  return <div ref={containerRef} className="map-canvas" />
}
