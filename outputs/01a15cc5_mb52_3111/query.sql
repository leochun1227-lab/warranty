
WITH stock AS (
 SELECT MANDT, MATNR, WERKS, LGORT, '' CHARG, '' SOBKZ, '' VBELN, '' POSNR,
 LABST unrestricted, INSME quality, SPEME blocked, UMLME transfer, EINME restricted, RETME return_qty
 FROM SAPHANADB.NSDM_V_MARD WHERE MANDT=? AND WERKS='3111' AND (MATNR LIKE 'Z%' OR MATNR LIKE 'D%')
 AND (LABST<>0 OR INSME<>0 OR SPEME<>0 OR UMLME<>0 OR EINME<>0 OR RETME<>0)
 UNION ALL
 SELECT MANDT, MATNR, WERKS, LGORT, CHARG, SOBKZ, VBELN, POSNR,
 KALAB, KAINS, KASPE, 0, KAEIN, 0
 FROM SAPHANADB.NSDM_V_MSKA WHERE MANDT=? AND WERKS='3111' AND (MATNR LIKE 'Z%' OR MATNR LIKE 'D%')
 AND (KALAB<>0 OR KAINS<>0 OR KASPE<>0 OR KAEIN<>0)
)
SELECT s.MATNR "Material", COALESCE(e.MAKTX,z.MAKTX,'') "Material Description",
 COALESCE(z.MAKTX,'') "Material Description CN",s.WERKS "Plant",
 s.LGORT "Storage Location",COALESCE(l.LGOBE,'') "Storage Location Description",
 s.CHARG "Batch",s.SOBKZ "Special Stock",s.VBELN "Sales Order",s.POSNR "Sales Order Item",
 a.MEINS "Base Unit",s.unrestricted "Unrestricted",s.quality "Quality Inspection",
 s.blocked "Blocked",s.transfer "Stock in Transfer",s.restricted "Restricted Use",s.return_qty "Returns"
FROM stock s
LEFT JOIN SAPHANADB.MARA a ON a.MANDT=s.MANDT AND a.MATNR=s.MATNR
LEFT JOIN SAPHANADB.MAKT e ON e.MANDT=s.MANDT AND e.MATNR=s.MATNR AND e.SPRAS='E'
LEFT JOIN SAPHANADB.MAKT z ON z.MANDT=s.MANDT AND z.MATNR=s.MATNR AND z.SPRAS='1'
LEFT JOIN SAPHANADB.T001L l ON l.MANDT=s.MANDT AND l.WERKS=s.WERKS AND l.LGORT=s.LGORT
ORDER BY s.MATNR,s.LGORT,s.SOBKZ,s.VBELN,s.POSNR,s.CHARG
