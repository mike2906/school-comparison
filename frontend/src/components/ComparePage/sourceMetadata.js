export function getProvenanceSourceKey(source, index) {
  return [
    source?.source_type || 'unknown',
    source?.source_url || '',
    source?.last_verified || '',
    source?.confidence || '',
    index,
  ].join('|')
}
