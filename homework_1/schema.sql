-- ==========================================================================
-- schema.sql  --  inventory table + seed rows for Tool 1
-- Loaded once by database.init_db() via conn.executescript().
-- ==========================================================================

CREATE TABLE IF NOT EXISTS products (
    sku           TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT,
    stock_qty     INTEGER NOT NULL,
    unit_price    REAL NOT NULL,
    reorder_level INTEGER DEFAULT 0
);

INSERT OR IGNORE INTO products
    (sku, name, category, stock_qty, unit_price, reorder_level)
VALUES
    -- Processors
    ('CPU-1001', 'AMD Ryzen 5 7600', 'processors', 42, 229.99, 10),
    ('CPU-1002', 'AMD Ryzen 7 7800X3D', 'processors', 18, 399.99, 6),
    ('CPU-1003', 'Intel Core i5-14600K', 'processors', 25, 319.99, 8),
    ('CPU-1004', 'Intel Core i7-14700K', 'processors', 12, 409.99, 5),
    ('CPU-1005', 'AMD Ryzen 9 7950X', 'processors', 9, 549.99, 3),
    ('CPU-1006', 'Intel Core i9-14900K', 'processors', 7, 589.99, 3),
    ('CPU-1007', 'AMD Ryzen 5 5600X', 'processors', 35, 149.99, 10),

    -- Graphics cards
    ('GPU-2001', 'NVIDIA GeForce RTX 4060 8GB', 'graphics-cards', 20, 299.99, 6),
    ('GPU-2002', 'NVIDIA GeForce RTX 4070 SUPER 12GB', 'graphics-cards', 11, 599.99, 4),
    ('GPU-2003', 'AMD Radeon RX 7800 XT 16GB', 'graphics-cards', 14, 519.99, 4),
    ('GPU-2004', 'AMD Radeon RX 7600 8GB', 'graphics-cards', 23, 269.99, 7),
    ('GPU-2005', 'NVIDIA GeForce RTX 4080 SUPER 16GB', 'graphics-cards', 6, 999.99, 2),
    ('GPU-2006', 'AMD Radeon RX 7900 XTX 24GB', 'graphics-cards', 8, 929.99, 3),
    ('GPU-2007', 'Intel Arc A750 8GB', 'graphics-cards', 19, 219.99, 5),

    -- Motherboards
    ('MB-3001', 'ASUS TUF Gaming B650-PLUS WiFi', 'motherboards', 16, 189.99, 5),
    ('MB-3002', 'MSI MAG Z790 Tomahawk WiFi', 'motherboards', 10, 259.99, 4),
    ('MB-3003', 'Gigabyte B760M DS3H AX', 'motherboards', 21, 139.99, 6),
    ('MB-3004', 'ASRock X670E Steel Legend', 'motherboards', 11, 279.99, 4),
    ('MB-3005', 'ASUS ROG Strix B550-F Gaming WiFi II', 'motherboards', 18, 169.99, 5),
    ('MB-3006', 'MSI PRO H610M-G DDR4', 'motherboards', 27, 89.99, 8),

    -- Memory
    ('RAM-4001', 'Corsair Vengeance 32GB DDR5-6000', 'memory', 55, 109.99, 15),
    ('RAM-4002', 'Kingston Fury Beast 16GB DDR5-5600', 'memory', 67, 64.99, 20),
    ('RAM-4003', 'G.Skill Ripjaws 32GB DDR4-3600', 'memory', 38, 79.99, 12),
    ('RAM-4004', 'Corsair Dominator Platinum 64GB DDR5-6000', 'memory', 13, 229.99, 4),
    ('RAM-4005', 'Kingston Fury Beast 32GB DDR4-3200', 'memory', 44, 72.99, 12),
    ('RAM-4006', 'G.Skill Trident Z5 RGB 32GB DDR5-6400', 'memory', 26, 139.99, 8),

    -- Storage
    ('SSD-5001', 'Samsung 990 PRO 1TB NVMe SSD', 'storage', 46, 119.99, 12),
    ('SSD-5002', 'WD Black SN850X 2TB NVMe SSD', 'storage', 31, 169.99, 10),
    ('SSD-5003', 'Crucial P3 Plus 1TB NVMe SSD', 'storage', 72, 74.99, 20),
    ('HDD-5004', 'Seagate Barracuda 4TB Hard Drive', 'storage', 29, 89.99, 8),
    ('SSD-5005', 'Samsung 870 EVO 2TB SATA SSD', 'storage', 33, 159.99, 10),
    ('SSD-5006', 'Crucial T500 2TB NVMe SSD', 'storage', 22, 149.99, 7),
    ('SSD-5007', 'Kingston NV2 500GB NVMe SSD', 'storage', 81, 39.99, 25),
    ('HDD-5008', 'Western Digital Blue 2TB Hard Drive', 'storage', 48, 59.99, 15),

    -- Power supplies
    ('PSU-6001', 'Corsair RM750e 750W Gold PSU', 'power-supplies', 34, 109.99, 10),
    ('PSU-6002', 'Seasonic Focus GX-850 850W Gold PSU', 'power-supplies', 17, 149.99, 5),
    ('PSU-6003', 'be quiet! Pure Power 12 M 1000W PSU', 'power-supplies', 9, 189.99, 3),
    ('PSU-6004', 'EVGA SuperNOVA 650W Gold PSU', 'power-supplies', 25, 89.99, 7),
    ('PSU-6005', 'Thermaltake Toughpower GF3 1200W PSU', 'power-supplies', 8, 219.99, 3),

    -- Cases
    ('CASE-7001', 'NZXT H5 Flow Mid-Tower Case', 'cases', 24, 94.99, 7),
    ('CASE-7002', 'Corsair 4000D Airflow Case', 'cases', 28, 104.99, 8),
    ('CASE-7003', 'Fractal Design North Case', 'cases', 13, 139.99, 4),
    ('CASE-7004', 'Lian Li O11 Dynamic EVO Case', 'cases', 14, 169.99, 4),
    ('CASE-7005', 'Cooler Master NR200P Mini-ITX Case', 'cases', 20, 109.99, 6),
    ('CASE-7006', 'Phanteks Eclipse G360A Case', 'cases', 17, 99.99, 5),

    -- Cooling
    ('COOL-8001', 'Noctua NH-D15 CPU Cooler', 'cooling', 19, 119.99, 5),
    ('COOL-8002', 'Arctic Liquid Freezer III 240', 'cooling', 15, 99.99, 5),
    ('COOL-8003', 'Cooler Master SickleFlow 120mm Fan', 'cooling', 85, 12.99, 25),
    ('COOL-8004', 'DeepCool AK620 CPU Cooler', 'cooling', 31, 64.99, 9),
    ('COOL-8005', 'Corsair iCUE H150i Elite 360mm AIO', 'cooling', 12, 189.99, 4),
    ('COOL-8006', 'Thermal Grizzly Kryonaut Thermal Paste', 'cooling', 90, 11.99, 30),

    -- Monitors
    ('MON-9001', 'LG UltraGear 27-inch 1440p 165Hz Monitor', 'monitors', 12, 299.99, 4),
    ('MON-9002', 'ASUS TUF 24-inch 1080p 180Hz Monitor', 'monitors', 21, 179.99, 6),
    ('MON-9003', 'Dell UltraSharp 32-inch 4K Monitor', 'monitors', 9, 749.99, 3),
    ('MON-9004', 'Samsung Odyssey G5 32-inch 1440p 165Hz Monitor', 'monitors', 14, 329.99, 4),
    ('MON-9005', 'AOC 24G2SP 24-inch 1080p 165Hz Monitor', 'monitors', 26, 159.99, 7),

    -- Peripherals
    ('KEY-9101', 'Keychron C2 Mechanical Keyboard', 'peripherals', 36, 69.99, 10),
    ('KEY-9102', 'Corsair K70 RGB Mechanical Keyboard', 'peripherals', 23, 129.99, 7),
    ('MOU-9201', 'Logitech G502 X Gaming Mouse', 'peripherals', 41, 79.99, 12),
    ('MOU-9202', 'Razer DeathAdder V3 Gaming Mouse', 'peripherals', 37, 69.99, 10),
    ('HST-9301', 'HyperX Cloud III Gaming Headset', 'peripherals', 28, 99.99, 8),
    ('MIC-9401', 'Elgato Wave 3 USB Microphone', 'peripherals', 16, 149.99, 5),

    -- Networking
    ('NET-9501', 'TP-Link Archer TX3000E WiFi 6 Adapter', 'networking', 42, 49.99, 12),
    ('NET-9502', 'ASUS XG-C100C 10Gb Ethernet Adapter', 'networking', 15, 99.99, 5),
    ('NET-9503', 'TP-Link 2.5Gb PCIe Ethernet Adapter', 'networking', 34, 29.99, 10),
    ('NET-9504', 'ASUS USB-AX56 WiFi 6 USB Adapter', 'networking', 29, 54.99, 8),
    ('NET-9505', 'Intel AX210 WiFi 6E PCIe Adapter', 'networking', 38, 39.99, 10),

    -- Cables
    ('CAB-9601', 'DisplayPort 1.4 Cable 2m', 'cables', 110, 14.99, 30),
    ('CAB-9602', 'HDMI 2.1 Cable 2m', 'cables', 125, 16.99, 35),
    ('CAB-9603', 'SATA III Data Cable 45cm', 'cables', 140, 5.99, 40),
    ('CAB-9604', 'USB-C 100W Charging Cable 2m', 'cables', 95, 12.99, 25),
    ('CAB-9605', 'PCIe 5.0 GPU Power Cable', 'cables', 53, 19.99, 15);