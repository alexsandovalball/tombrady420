-- Restructure Concentrates Subcategories Migration
-- Run this once against catalog.db after deploying the code changes

-- Solventless (Rosin)
UPDATE products SET subcategory = 'SOLVENTLESS'
WHERE category = 'CONCENTRATES' AND subcategory = 'ROSIN';

-- Live Resin & Extracts (Live Resin, Shatter, Badder, Crumble, Sugar, Diamonds and Sauce)
UPDATE products SET subcategory = 'LIVE RESIN AND EXTRACTS'
WHERE category = 'CONCENTRATES' AND subcategory IN ('LIVE RESIN', 'SHATTER', 'BADDER', 'CRUMBLE', 'SUGAR', 'DIAMONDS AND SAUCE');

-- Oils & Isolates (Distillate, RSO, THCa Powder)
UPDATE products SET subcategory = 'OILS AND ISOLATES'
WHERE category = 'CONCENTRATES' AND subcategory IN ('DISTILLATE', 'RSO', 'THCAPOWDER');

-- Hash & Kief
UPDATE products SET subcategory = 'HASH AND KIEF'
WHERE category = 'CONCENTRATES' AND subcategory IN ('HASH', 'KIEF');

-- Others stays as OTHERS

-- Rename Infused Flower to Moonrocks and Snowcaps
UPDATE products SET subcategory = 'MOONROCKS AND SNOWCAPS'
WHERE category = 'FLOWER' AND subcategory = 'INFUSED FLOWER';
