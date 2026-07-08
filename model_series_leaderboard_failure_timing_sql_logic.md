# Model Series Leaderboard + Failure Timing SQL Logic

整理来源：

- `C:\Users\Leo.Li\Documents\GitHub\warranty\fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py`
- 对应前端消费页：`C:\Users\Leo.Li\Documents\GitHub\warranty\analysis.html`

这份文件把两块逻辑放到一起：

1. `Model Series Leaderboard` 的 vehicle base / denominator SQL
2. `Failure timing (delivery -> ticket)` 的 dispatch / PGI SQL

## 1. 整体逻辑顺序

后端执行顺序是：

1. 先按 `Ticket Serial / Chassis` 找车辆 dispatch date
2. 再按 `Sales Order` 做 fallback 找 dispatch date
3. 再拉整车 base：
   - 已发车 shipped
   - 库存 in-stock
   - 在途 in-transit
   - 以及 legacy 的 `LIKP.WADAT_IST` PGI
4. 后端把结果写到：
   - ticket 级别的 `Vehicle Dispatch Date / Source / Serial / Sales Order`
   - analysis 用的 `analysisVehicleBaseSummary`
5. 前端时间优先级：
   - `Vehicle Dispatch Date`
   - `pgiByChassis`
   - `pgiBySalesOrder`
   - `Date of Purchase` fallback

## 2. Failure Timing SQL 1: 按 Serial / Chassis 直接匹配 dispatch

用途：

- 优先用 ticket 上的 `SerialID / ChassisNumber`
- 通过 `OBJK / EQUI / ZTSD002` 找到真实 serial
- 再通过 `SER02 -> VBAK/VBAP -> LIPS/LIKP` 找最早 dispatch date

```sql
WITH input_candidates AS (
    -- Python 动态拼接:
    -- SELECT '{candidate1}' AS "InputCandidate" FROM DUMMY
    -- UNION ALL
    -- SELECT '{candidate2}' AS "InputCandidate" FROM DUMMY
),
matched_units AS (
    SELECT DISTINCT
        i."InputCandidate",
        o."SERNR" AS "MatchedSerial"
    FROM input_candidates i
    INNER JOIN "SAPHANADB"."OBJK" o
        ON o."MANDT" = '{SAP_CLIENT}'
       AND o."SERNR" = i."InputCandidate"

    UNION

    SELECT DISTINCT
        i."InputCandidate",
        e."SERNR" AS "MatchedSerial"
    FROM input_candidates i
    INNER JOIN "SAPHANADB"."EQUI" e
        ON e."MANDT" = '{SAP_CLIENT}'
       AND e."SERNR" = i."InputCandidate"

    UNION

    SELECT DISTINCT
        i."InputCandidate",
        z."SERNR" AS "MatchedSerial"
    FROM input_candidates i
    INNER JOIN "SAPHANADB"."ZTSD002" z
        ON z."MANDT" = '{SAP_CLIENT}'
       AND z."WERKS" = '3091'
       AND z."SERNR2" = i."InputCandidate"
),
vehicle_so AS (
    SELECT DISTINCT
        mu."InputCandidate",
        mu."MatchedSerial",
        s2."SDAUFNR" AS "Sales Order"
    FROM matched_units mu
    INNER JOIN "SAPHANADB"."OBJK" o
        ON o."MANDT" = '{SAP_CLIENT}'
       AND o."SERNR" = mu."MatchedSerial"
    INNER JOIN "SAPHANADB"."SER02" s2
        ON s2."MANDT" = o."MANDT"
       AND s2."OBKNR" = o."OBKNR"
       AND LPAD(TO_VARCHAR(s2."POSNR"), 6, '0') = '000010'
    INNER JOIN "SAPHANADB"."VBAK" vbak
        ON vbak."MANDT" = s2."MANDT"
       AND vbak."VBELN" = s2."SDAUFNR"
       AND vbak."VKORG" = '{SALES_ORG}'
    INNER JOIN "SAPHANADB"."VBAP" vbap
        ON vbap."MANDT" = s2."MANDT"
       AND vbap."VBELN" = s2."SDAUFNR"
       AND LPAD(TO_VARCHAR(vbap."POSNR"), 6, '0') = '000010'
       AND vbap."MATNR" LIKE 'Z%'
),
vehicle_pgi AS (
    SELECT DISTINCT
        vs."InputCandidate",
        vs."MatchedSerial",
        vs."Sales Order",
        lips."VBELN" AS "Delivery Doc",
        likp."WADAT_IST" AS "Dispatch Date"
    FROM vehicle_so vs
    INNER JOIN "SAPHANADB"."LIPS" lips
        ON lips."MANDT" = '{SAP_CLIENT}'
       AND lips."VGBEL" = vs."Sales Order"
       AND LPAD(TO_VARCHAR(lips."VGPOS"), 6, '0') = '000010'
    INNER JOIN "SAPHANADB"."LIKP" likp
        ON likp."MANDT" = lips."MANDT"
       AND likp."VBELN" = lips."VBELN"
    WHERE likp."WADAT_IST" IS NOT NULL
      AND likp."WADAT_IST" <> '00000000'
)
SELECT
    vp."InputCandidate",
    vp."MatchedSerial",
    MIN(vp."Dispatch Date") AS "VehicleDispatchDate",
    MIN(vp."Delivery Doc") AS "VehicleDispatchDeliveryDoc",
    MIN(vp."Sales Order") AS "VehicleDispatchSalesOrder"
FROM vehicle_pgi vp
GROUP BY
    vp."InputCandidate",
    vp."MatchedSerial"
ORDER BY
    vp."InputCandidate",
    MIN(vp."Dispatch Date");
```

后处理逻辑：

- 同一个 candidate 保留最早 `VehicleDispatchDate`
- 同一个 ticket 优先级：
  - `serial_direct`
  - `chassis_direct`

## 3. Failure Timing SQL 2: 按 Sales Order fallback 匹配 dispatch

用途：

- 当 ticket 没有直接匹配到 serial/chassis dispatch 时
- 用 `Sales Order` 继续反查 serial 和 dispatch date

```sql
WITH input_so AS (
    -- Python 动态拼接:
    -- SELECT '{sales_order1}' AS "Sales Order" FROM DUMMY
    -- UNION ALL
    -- SELECT '{sales_order2}' AS "Sales Order" FROM DUMMY
),
serial_from_so AS (
    SELECT DISTINCT
        i."Sales Order",
        o."SERNR" AS "MatchedSerial"
    FROM input_so i
    INNER JOIN "SAPHANADB"."VBAK" vbak
        ON vbak."MANDT" = '{SAP_CLIENT}'
       AND vbak."VBELN" = i."Sales Order"
       AND vbak."VKORG" = '{SALES_ORG}'
    INNER JOIN "SAPHANADB"."VBAP" vbap
        ON vbap."MANDT" = vbak."MANDT"
       AND vbap."VBELN" = vbak."VBELN"
       AND LPAD(TO_VARCHAR(vbap."POSNR"), 6, '0') = '000010'
       AND vbap."MATNR" LIKE 'Z%'
    INNER JOIN "SAPHANADB"."SER02" s2
        ON s2."MANDT" = '{SAP_CLIENT}'
       AND s2."SDAUFNR" = i."Sales Order"
       AND LPAD(TO_VARCHAR(s2."POSNR"), 6, '0') = '000010'
    INNER JOIN "SAPHANADB"."OBJK" o
        ON o."MANDT" = s2."MANDT"
       AND o."OBKNR" = s2."OBKNR"
),
vehicle_pgi AS (
    SELECT DISTINCT
        so."Sales Order",
        so."MatchedSerial",
        lips."VBELN" AS "Delivery Doc",
        likp."WADAT_IST" AS "Dispatch Date"
    FROM serial_from_so so
    INNER JOIN "SAPHANADB"."LIPS" lips
        ON lips."MANDT" = '{SAP_CLIENT}'
       AND lips."VGBEL" = so."Sales Order"
       AND LPAD(TO_VARCHAR(lips."VGPOS"), 6, '0') = '000010'
    INNER JOIN "SAPHANADB"."LIKP" likp
        ON likp."MANDT" = lips."MANDT"
       AND likp."VBELN" = lips."VBELN"
    WHERE likp."WADAT_IST" IS NOT NULL
      AND likp."WADAT_IST" <> '00000000'
)
SELECT
    "Sales Order",
    "MatchedSerial",
    MIN("Dispatch Date") AS "VehicleDispatchDate",
    MIN("Delivery Doc") AS "VehicleDispatchDeliveryDoc"
FROM vehicle_pgi
GROUP BY
    "Sales Order",
    "MatchedSerial"
ORDER BY
    "Sales Order",
    MIN("Dispatch Date");
```

后处理逻辑：

- 每个 `Sales Order` 保留最早 `VehicleDispatchDate`
- 当前端 ticket 级 dispatch 为空时，才用这条结果做 fallback

## 4. Leaderboard / Failure Timing 共用 SQL 3: 已发车 shipped base + PGI

用途：

- 给 `Model Series Leaderboard` 提供真正 denominator
- 也给 `Failure timing` 提供 `pgiByChassis`

```sql
WITH pgi AS (
    SELECT
        "KDAUF" AS "SalesOrder",
        "MATNR" AS "Material",
        MAX("BUDAT_MKPF") AS "PGIDate"
    FROM "SAPHANADB"."NSDM_V_MSEG"
    WHERE "MANDT" = '{mandt}'
      AND "WERKS" = '3111'
      AND "KDPOS" = 10
      AND "KDAUF" IS NOT NULL
      AND COALESCE("MATNR",'') LIKE 'Z%'
      AND "BWART" IN ('601','602','641','642','645','647','633','634')
    GROUP BY "KDAUF","MATNR"
)
SELECT DISTINCT
    p."SalesOrder"                 AS "Sales Order",
    p."Material"                   AS "Material",
    v."ARKTX"                      AS "Description",
    o."SERNR"                      AS "Serial",
    z."SERNR2"                     AS "VIN",
    TO_VARCHAR(p."PGIDate")        AS "PGI Date"
FROM pgi p
LEFT JOIN "SAPHANADB"."VBAP" v
    ON v."MANDT" = '{mandt}'
   AND v."VBELN" = p."SalesOrder"
   AND v."MATNR" = p."Material"
   AND LPAD(TO_VARCHAR(v."POSNR"), 6, '0') = '000010'
LEFT JOIN "SAPHANADB"."SER02" s2
    ON s2."MANDT" = '{mandt}'
   AND s2."SDAUFNR" = p."SalesOrder"
   AND LPAD(TO_VARCHAR(s2."POSNR"), 6, '0') = '000010'
LEFT JOIN "SAPHANADB"."OBJK" o
    ON o."MANDT" = s2."MANDT"
   AND o."OBKNR" = s2."OBKNR"
LEFT JOIN "SAPHANADB"."ZTSD002" z
    ON z."MANDT" = o."MANDT"
   AND z."WERKS" = '3091'
   AND z."SERNR" = o."SERNR"
WHERE p."PGIDate" >= '{cutoff}';
```

要点：

- 来源表是 `NSDM_V_MSEG`
- `BWART` 用的是 outbound movement
- 这条是当前真正主路线，优先级高于 legacy `LIKP`

## 5. Leaderboard SQL 4: 库存 in-stock base

用途：

- 统计 3111 工厂当前库存车
- 这些车也进入 denominator

```sql
WITH mv AS (
    SELECT
        "KDAUF","MATNR","LGORT",
        MAX("BUDAT_MKPF") AS "LastMvmt",
        MIN("BUDAT_MKPF") AS "FirstMvmt"
    FROM "SAPHANADB"."NSDM_V_MSEG"
    WHERE "MANDT" = '{mandt}'
      AND "WERKS" = '3111'
      AND "KDPOS" = 10
    GROUP BY "KDAUF","MATNR","LGORT"
)
SELECT
    a."VBELN"                      AS "Sales Order",
    a."MATNR"                      AS "Material",
    v."ARKTX"                      AS "Description",
    a."LGORT"                      AS "Storage Location",
    a."KALAB"                      AS "Stock Qty",
    TO_VARCHAR(mv."LastMvmt")      AS "Last Movement",
    TO_VARCHAR(mv."FirstMvmt")     AS "First Movement",
    o."SERNR"                      AS "Serial",
    z."SERNR2"                     AS "VIN"
FROM "SAPHANADB"."NSDM_V_MSKA" a
LEFT JOIN mv
    ON a."VBELN" = mv."KDAUF"
   AND a."MATNR" = mv."MATNR"
   AND a."LGORT" = mv."LGORT"
LEFT JOIN "SAPHANADB"."VBAP" v
    ON v."MANDT" = '{mandt}'
   AND v."VBELN" = a."VBELN"
   AND v."MATNR" = a."MATNR"
   AND LPAD(TO_VARCHAR(v."POSNR"), 6, '0') = '000010'
LEFT JOIN "SAPHANADB"."SER02" s2
    ON s2."MANDT" = '{mandt}'
   AND s2."SDAUFNR" = a."VBELN"
   AND LPAD(TO_VARCHAR(s2."POSNR"), 6, '0') = '000010'
LEFT JOIN "SAPHANADB"."OBJK" o
    ON o."MANDT" = s2."MANDT"
   AND o."OBKNR" = s2."OBKNR"
LEFT JOIN "SAPHANADB"."ZTSD002" z
    ON z."MANDT" = o."MANDT"
   AND z."WERKS" = '3091'
   AND z."SERNR" = o."SERNR"
WHERE a."MANDT" = '{mandt}'
  AND a."WERKS" = 3111
  AND a."MATNR" LIKE 'Z%'
  AND a."LGORT" IN ('0024','0026')
  AND a."KALAB" <> 0;
```

## 6. Leaderboard SQL 5: 在途 in-transit base

用途：

- 统计 open PO 且还没有 GR 的车辆
- 也计入 denominator

```sql
WITH mseg_gr AS (
    SELECT
        "MANDT","EBELN","EBELP",
        MIN("BUDAT_MKPF") AS "GR_DATE"
    FROM "SAPHANADB"."NSDM_V_MSEG"
    WHERE "MANDT" = '{mandt}'
      AND "EBELN" IS NOT NULL
      AND "BWART" IN ('101','103')
    GROUP BY "MANDT","EBELN","EBELP"
),
ekpo_open_nogr AS (
    SELECT
        p."MANDT",
        p."EBELN",
        p."EBELP",
        p."CREATIONDATE",
        SUBSTRING(
            p."TXZ01",
            1,
            CASE
                WHEN INSTR(p."TXZ01", ' ') > 0 THEN INSTR(p."TXZ01", ' ') - 1
                ELSE LENGTH(p."TXZ01")
            END
        ) AS "SERNR_PREFIX"
    FROM "SAPHANADB"."EKPO" p
    JOIN "SAPHANADB"."EKKO" h
        ON h."MANDT" = p."MANDT"
       AND h."EBELN" = p."EBELN"
    LEFT JOIN mseg_gr gr
        ON gr."MANDT" = p."MANDT"
       AND gr."EBELN" = p."EBELN"
       AND gr."EBELP" = p."EBELP"
    WHERE
        p."MANDT" = '{mandt}'
        AND p."WERKS"='3111'
        AND p."MATKL"='Z003'
        AND LOWER(p."TXZ01") LIKE '% to %'
        AND COALESCE(p."LOEKZ",'') = ''
        AND COALESCE(h."LOEKZ",'') = ''
        AND COALESCE(p."ELIKZ",'') <> 'X'
        AND gr."EBELN" IS NULL
)
SELECT
    vbap."VBELN"                   AS "Sales Order",
    vbap."MATNR"                   AS "Material",
    vbap."ARKTX"                   AS "Description",
    objk."SERNR"                   AS "Serial",
    ek."EBELN"                     AS "PO No",
    TO_VARCHAR(ek."CREATIONDATE")  AS "PO Date"
FROM "SAPHANADB"."VBAP" vbap
LEFT JOIN "SAPHANADB"."SER02" s
    ON vbap."MANDT" = s."MANDT"
   AND vbap."VBELN" = s."SDAUFNR"
   AND s."POSNR" = 10
LEFT JOIN "SAPHANADB"."OBJK" objk
    ON s."MANDT" = objk."MANDT"
   AND s."OBKNR" = objk."OBKNR"
INNER JOIN ekpo_open_nogr ek
    ON ek."MANDT" = vbap."MANDT"
   AND objk."SERNR" = ek."SERNR_PREFIX"
WHERE
    vbap."MANDT" = '{mandt}'
    AND vbap."POSNR" = 10;
```

## 7. Leaderboard / Failure Timing SQL 6: legacy LIKP PGI fallback

用途：

- 作为老路线保留
- 当 MSEG PGI 没有时，仍可提供 PGI date

```sql
SELECT DISTINCT
    s2."SDAUFNR" AS "Sales Order",
    o."SERNR" AS "Serial",
    z."SERNR2" AS "VIN",
    vbap."MATNR" AS "Material",
    vbap."ARKTX" AS "Description",
    lips."VBELN" AS "Delivery Doc",
    likp."WADAT_IST" AS "PGI Date"
FROM "SAPHANADB"."SER02" s2
INNER JOIN "SAPHANADB"."OBJK" o
    ON o."MANDT" = s2."MANDT"
   AND o."OBKNR" = s2."OBKNR"
INNER JOIN "SAPHANADB"."VBAK" vbak
    ON vbak."MANDT" = s2."MANDT"
   AND vbak."VBELN" = s2."SDAUFNR"
   AND vbak."VKORG" = '{SALES_ORG}'
INNER JOIN "SAPHANADB"."VBAP" vbap
    ON vbap."MANDT" = s2."MANDT"
   AND vbap."VBELN" = s2."SDAUFNR"
   AND LPAD(TO_VARCHAR(vbap."POSNR"), 6, '0') = '000010'
   AND vbap."MATNR" LIKE 'Z%'
LEFT JOIN "SAPHANADB"."ZTSD002" z
    ON z."MANDT" = o."MANDT"
   AND z."WERKS" = '3091'
   AND z."SERNR" = o."SERNR"
INNER JOIN "SAPHANADB"."LIPS" lips
    ON lips."MANDT" = s2."MANDT"
   AND lips."VGBEL" = s2."SDAUFNR"
   AND LPAD(TO_VARCHAR(lips."VGPOS"), 6, '0') = '000010'
INNER JOIN "SAPHANADB"."LIKP" likp
    ON likp."MANDT" = lips."MANDT"
   AND likp."VBELN" = lips."VBELN"
WHERE s2."MANDT" = '{SAP_CLIENT}'
  AND LPAD(TO_VARCHAR(s2."POSNR"), 6, '0') = '000010'
  AND likp."WADAT_IST" IS NOT NULL
  AND likp."WADAT_IST" <> '00000000'
  AND likp."WADAT_IST" >= '{cutoff_yyyymmdd}';
```

## 8. SQL 结果如何汇总成前端可用数据

后端最终不是直接把 SQL 结果原样给前端，而是再汇总成两个对象：

### 8.1 Vehicle base summary

- `seriesBase`
  - 每个 model series 的总 base
  - 计算规则：`shipped + inStock + inTransit`
- `seriesBaseBreakdown`
  - 每个 series 的 shipped / inStock / inTransit 拆分
- `pgiByChassis`
  - `serial/chassis -> PGI date`
- `pgiBySalesOrder`
  - `sales order -> PGI date`

### 8.2 Ticket-level dispatch fields

- `Vehicle Dispatch Date`
- `Vehicle Dispatch Source`
- `Vehicle Dispatch Serial`
- `Vehicle Dispatch Sales Order`
- `Vehicle Dispatch Delivery Doc`

## 9. 前端实际时间优先级

前端 `analysis.html` 里，`Failure timing (delivery -> ticket)` 最终用的是下面顺序：

1. ticket 自身回写的 `Vehicle Dispatch Date`
2. `pgiByChassis`
3. `pgiBySalesOrder`
4. `Date of Purchase`

最终天数算法：

```text
Failure Days = Created On - Delivery Date
```

分桶规则：

- `0 - 30 days`
- `31 - 90 days`
- `91 - 180 days`
- `180+ days`

## 10. Leaderboard 指标计算口径

`Model Series Leaderboard` 里最关键的是：

```text
Vehicle Base = manual override
            else seriesBase
            else traced chassis from ticket rows
```

```text
Repair Rate = repaired vehicles / vehicle base
Avg Repairs Per Van = tickets / repaired vehicles
Cost Per Vehicle = warranty cost / vehicle base
```

所以 SQL 对 Leaderboard 的关键影响点是：

- `seriesBase` 是否完整
- `shipped / inStock / inTransit` 是否都取到
- `pgiByChassis / pgiBySalesOrder` 是否补齐 failure timing

## 11. 当前这套逻辑的核心结论

- `Failure timing` 不只看 `LIKP.WADAT_IST`
- 当前主路线已经改成：
  - direct dispatch
  - MSEG PGI
  - sales order PGI fallback
  - purchase date fallback
- `Leaderboard` 的 denominator 也不只看 ticket 里出现过的车
- 当前 denominator 是 SAP 拉出来的真实车量 base，而不是“出过故障的车数”

