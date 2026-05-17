/**
 * Unterturkheim 3D City Model - Metadata API Server
 */

const express = require('express');
const { Pool } = require('pg');
const cors = require('cors');
const path = require('path');

const app = express();
app.use(cors());
app.use(express.json());
app.use('/photos', express.static(path.join(__dirname, 'photos')));

const pool = new Pool({
  connectionString: process.env.DATABASE_URL || 'postgresql://postgres.kxisoojfjyhcqvjxfgbl:Afghanistan@28911@@aws-1-eu-north-1.pooler.supabase.com:5432/postgres',
  ssl: { rejectUnauthorized: false }
});

app.get('/health', async (req, res) => {
  try {
    await pool.query('SELECT 1');
    res.json({ status: 'ok', database: 'connected' });
  } catch (err) {
    res.status(500).json({ status: 'error', message: err.message });
  }
});

// Click lookup by coordinates
app.get('/building', async (req, res) => {
  const lon = parseFloat(req.query.lon);
  const lat = parseFloat(req.query.lat);
  if (isNaN(lon) || isNaN(lat)) return res.status(400).json({ error: 'Invalid lon/lat' });
  try {
    const result = await pool.query(`
      SELECT
        "oid_" AS objekt_id, "aktualit" AS aktualitaet,
        "gebnutzbez" AS gebaeudetyp, "funktion" AS funktion,
        "fktkurz" AS funktion_kurz, "name" AS name,
        "anzahlgs" AS anzahl_geschosse, "lagebeztxt" AS adresse,
        "Shape_Area" AS flaeche_m2, "Tim_Collec" AS aufnahmedatum,
        "PHOTO" AS foto,
        -- Auto-generate photo filename from oid_ as fallback
        "oid_" || '.jpeg' AS foto_auto
      FROM "2D_ALKIS_Buildings"."ALKIS_2D_Buildings"
      WHERE ST_Contains(geom, ST_Transform(ST_SetSRID(ST_MakePoint($1,$2),4326),25832))
      LIMIT 1
    `, [lon, lat]);
    if (result.rows.length > 0) return res.json({ source: 'ALKIS_2D', attributes: result.rows[0] });
    return res.status(404).json({ source: 'no_alkis_match' });
  } catch (err) {
    console.error('[ERROR] /building:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// All buildings with centroids for coloring
app.get('/buildings/colors', async (req, res) => {
  try {
    const result = await pool.query(`
      SELECT
        "oid_" AS objekt_id,
        "gebnutzbez" AS gebaeudetyp,
        "funktion" AS funktion,
        "aktualit" AS aktualitaet,
        ROUND(ST_X(ST_Transform(ST_Centroid(geom),4326))::numeric,7) AS lon,
        ROUND(ST_Y(ST_Transform(ST_Centroid(geom),4326))::numeric,7) AS lat
      FROM "2D_ALKIS_Buildings"."ALKIS_2D_Buildings"
      WHERE geom IS NOT NULL
    `);
    res.json({ buildings: result.rows });
  } catch (err) {
    console.error('[ERROR] /buildings/colors:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// Year filter
app.get('/buildings/year', async (req, res) => {
  const from = parseInt(req.query.from);
  const to   = parseInt(req.query.to);
  if (isNaN(from) || isNaN(to)) return res.status(400).json({ error: 'Invalid from/to' });
  try {
    const result = await pool.query(`
      SELECT
        "oid_" AS objekt_id,
        "aktualit"::text AS aktualitaet,
        "gebnutzbez" AS gebaeudetyp,
        "lagebeztxt" AS adresse,
        ROUND(ST_X(ST_Transform(ST_Centroid(geom),4326))::numeric,7) AS lon,
        ROUND(ST_Y(ST_Transform(ST_Centroid(geom),4326))::numeric,7) AS lat
      FROM "2D_ALKIS_Buildings"."ALKIS_2D_Buildings"
      WHERE geom IS NOT NULL
        AND "aktualit" IS NOT NULL
        AND EXTRACT(YEAR FROM TO_DATE("aktualit", 'YYYY-MM-DD')) BETWEEN $1 AND $2
    `, [from, to]);
    res.json({ buildings: result.rows, count: result.rows.length });
  } catch (err) {
    console.error('[ERROR] /buildings/year:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// Year filter — returns converted gml:id list for 3D tile styling
app.get('/buildings/year/ids', async (req, res) => {
  const from = parseInt(req.query.from);
  const to   = parseInt(req.query.to);
  if (isNaN(from) || isNaN(to)) return res.status(400).json({ error: 'Invalid from/to' });
  try {
    const result = await pool.query(`
      SELECT
        "oid_",
        -- Convert DEBWL52210001EnpBL → DEBW_52210001Enp
        -- Remove leading 'DEBWL', remove trailing 'BL', add 'DEBW_' prefix
        'DEBW_' || SUBSTRING("oid_", 6, LENGTH("oid_") - 7) AS gml_id,
        "aktualit",
        "lagebeztxt" AS adresse,
        ROUND(ST_X(ST_Transform(ST_Centroid(geom),4326))::numeric,7) AS lon,
        ROUND(ST_Y(ST_Transform(ST_Centroid(geom),4326))::numeric,7) AS lat
      FROM "2D_ALKIS_Buildings"."ALKIS_2D_Buildings"
      WHERE geom IS NOT NULL
        AND "aktualit" IS NOT NULL
        AND EXTRACT(YEAR FROM TO_DATE("aktualit", 'YYYY-MM-DD')) BETWEEN $1 AND $2
    `, [from, to]);

    const gmlIds = result.rows.map(r => r.gml_id);
    res.json({
      count: result.rows.length,
      gml_ids: gmlIds,
      buildings: result.rows
    });
  } catch (err) {
    console.error('[ERROR] /buildings/year/ids:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// OID search
app.get('/search', async (req, res) => {
  const oid = req.query.oid;
  if (!oid) return res.status(400).json({ error: 'Missing oid' });
  try {
    const result = await pool.query(`
      SELECT
        "oid_" AS objekt_id, "aktualit" AS aktualitaet,
        "gebnutzbez" AS gebaeudetyp, "funktion" AS funktion,
        "fktkurz" AS funktion_kurz, "name" AS name,
        "anzahlgs" AS anzahl_geschosse, "lagebeztxt" AS adresse,
        "Shape_Area" AS flaeche_m2, "Tim_Collec" AS aufnahmedatum,
        "PHOTO" AS foto,
        ROUND(ST_X(ST_Transform(ST_Centroid(geom),4326))::numeric,7) AS lon,
        ROUND(ST_Y(ST_Transform(ST_Centroid(geom),4326))::numeric,7) AS lat
      FROM "2D_ALKIS_Buildings"."ALKIS_2D_Buildings"
      WHERE "oid_" ILIKE $1
      LIMIT 1
    `, [`%${oid}%`]);
    if (result.rows.length > 0) return res.json({ found: true, building: result.rows[0] });
    return res.status(404).json({ found: false, message: 'Not found' });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// LULC layers
app.get('/lulc', async (req, res) => {
  try {
    const tables = await pool.query(`
      SELECT table_name FROM information_schema.tables
      WHERE table_schema = 'LULC' AND table_type = 'BASE TABLE'
    `);

    const layers = [];
    for (const row of tables.rows) {
      const tbl = row.table_name;
      try {
        // Find geometry column
        const geomCol = await pool.query(`
          SELECT column_name FROM information_schema.columns
          WHERE table_schema='LULC' AND table_name=$1
          AND udt_name='geometry' LIMIT 1
        `, [tbl]);

        if (geomCol.rows.length === 0) { console.warn(`No geom in ${tbl}`); continue; }
        const gc = geomCol.rows[0].column_name;

        // Get non-geometry columns only
        const cols = await pool.query(`
          SELECT column_name FROM information_schema.columns
          WHERE table_schema='LULC' AND table_name=$1
          AND udt_name != 'geometry'
        `, [tbl]);
        const colList = cols.rows.map(c => `"${c.column_name}"`).join(', ');

        const geojson = await pool.query(`
          SELECT json_build_object(
            'type','FeatureCollection',
            'features', COALESCE(json_agg(
              json_build_object(
                'type','Feature',
                'geometry', ST_AsGeoJSON(ST_Transform("${gc}",4326))::json,
                'properties', json_build_object(${cols.rows.map(c=>`'${c.column_name}', "${c.column_name}"`).join(', ')})
              )
            ),'[]'::json)
          ) AS geojson
          FROM "LULC"."${tbl}"
        `);

        layers.push({ name: tbl, geojson: geojson.rows[0].geojson });
        console.log(`✅ LULC loaded: "${tbl}"`);
      } catch(e) {
        console.warn(`Skipping LULC "${tbl}":`, e.message);
      }
    }
    res.json({ layers });
  } catch (err) {
    console.error('[ERROR] /lulc:', err.message);
    res.status(500).json({ error: err.message });
  }
});

const PORT = 3000;
app.listen(PORT, () => {
  console.log(`✅  Metadata API  →  http://localhost:${PORT}`);
  console.log(`    Health check  →  http://localhost:${PORT}/health`);
});
