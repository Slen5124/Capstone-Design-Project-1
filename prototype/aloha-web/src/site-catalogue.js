/* Map authorized site records to the fields used by the store list and map. */
((root) => {
  function catalogue(sites, excludedCodes = []) {
    const excluded = new Set(excludedCodes);
    return sites
      .filter(site => site.has_schedule === true && !excluded.has(site.code))
      .map(site => ({
        id: site.code,
        name: site.name,
        city: site.address || '',
        timeZone: site.timezone,
        lat: site.latitude,
        lon: site.longitude,
        connected: true,
        completed: site.completed === true,
      }));
  }

  function mappable(site) {
    return Number.isFinite(site?.lat) && Number.isFinite(site?.lon)
      && Math.abs(site.lat) <= 90 && Math.abs(site.lon) <= 180;
  }

  const model = { catalogue, mappable };
  if (typeof module !== 'undefined') module.exports = model;
  else root.SiteCatalogue = model;
})(typeof window === 'undefined' ? globalThis : window);
