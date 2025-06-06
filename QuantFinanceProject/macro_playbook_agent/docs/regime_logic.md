# Regime Classification Logic

**Version 0.1** (to be done)  
Use simple rule-based tags as a first sanity check:

| Regime                 | Conditions                                                                |
|------------------------|---------------------------------------------------------------------------|
| **Crisis**             | GDP_QoQ < -2% **or** Crude_Vol30d > 0.05                                 |
| **Stagflation**        | CPI_Z > 1.0 **and** IIP_Z < 0                                        |
| **Overheating**        | CPI_Z > 1.0 **and** IIP_Z > 1.0                                       |
| **Reflation**          | 0.5 ≤ CPI_Z ≤ 1.0 **and** IIP_YoY > 3%                                |
| **Disinflationary Recovery** | CPI_Z < 0.5 **and** IIP_YoY > 0                            |
| **Neutral**            | None of the above                                                        |

---

## Future Versions

### Version 0.2  
- Incorporate **Crude_Delta** and **USDINR_Vol30d** into rules.  
- Tune thresholds to percentiles (e.g., CPI_Z > 75th percentile).  

### Version 1.0  
- Move to **unsupervised clustering** (k-means/HMM) on full signal set.  
- Map statistical clusters to human-readable regime names.  
- Validate against `regime_labels.csv` (2008, 2013, 2020, 2022).

---

_End of Regime Logic Documentation_  
