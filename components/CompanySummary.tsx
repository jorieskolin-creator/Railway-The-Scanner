
const { ResponsiveContainer, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, Tooltip: RechartsTooltip, LineChart, Line, XAxis, YAxis, CartesianGrid, ReferenceLine } = (window as any).Recharts;
const { ExternalLink, CheckCircle, AlertTriangle, ShieldCheck, ServerCrash, RefreshCw, XCircle, Search, Zap, Scale, Activity, Download, Copy, FileJson, FileCode, Check, Eye, AlertOctagon, Info, Sparkles, TrendingUp } = (window as any).Lucide;
const React = (window as any).React;
const { useRef, useState, useEffect } = React;

const HighlightText = ({ text }) => {
  if (!text) return null;
  
  const negativeTerms = [
    "Digital Taylorism", "Feature Factory", "Command and Control", "Zombie Scrum", 
    "Fake Agile", "Silos", "Micromanagement", "Project-based", "Fear", "Bureaucracy", 
    "Legacy", "Cash Cow", "At Risk", "Surveillance", "Turf Wars", "Water-scrum-fall",
    "Ticket-based", "Output-driven", "Gatekeeping", "Compliance-driven", "Blame Culture",
    "Risk-averse", "Siloed", "Waterfall", "Consulting-heavy", "Vendor Lock-in", "Burnout",
    "Change Control Board", "HiPPO", "Modernization Washing", "AI Washing", "Agile Washing",
    "Customer complaints", "Poor reviews", "Service failures", "Customer churn"
  ];

  const positiveTerms = [
    "Psychological Safety", "Platform Engineering", "Flow Efficiency", "Autonomy", 
    "Value Streams", "Outcome-based", "Trust", "Innovation", "Modernization", 
    "Thriving", "Modernizing", "Distributed Authority", "Sense and Respond",
    "Stream-aligned", "Zero Distance", "Customer Intimacy", "Continuous Delivery",
    "DevOps", "Data Mesh", "Empowerment", "Learning Organization", "Servant Leadership",
    "Cloud-native", "Ecosystem", "Open Source", "Self-organization", "Cross-functional",
    "Adaptive Strategy", "Generative AI", "AI-Augmented",
    "Customer Experience", "Customer Journey", "Voice of Customer", "Net Promoter",
    "Omnichannel", "Personalization", "Customer-first"
  ];

  const sortTerms = (a, b) => b.length - a.length;
  negativeTerms.sort(sortTerms);
  positiveTerms.sort(sortTerms);
  
  const allTerms = [...negativeTerms, ...positiveTerms];
  const escapedTerms = allTerms.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  
  const regex = new RegExp(`(${escapedTerms.join('|')})`, 'gi');
  const parts = text.split(regex);

  return (
    <>
      {parts.map((part, i) => {
        const lowerPart = part.toLowerCase();
        const isNegative = negativeTerms.some(term => term.toLowerCase() === lowerPart);
        const isPositive = positiveTerms.some(term => term.toLowerCase() === lowerPart);

        if (isNegative) {
             return <span key={i} className="font-bold text-rose-300 bg-rose-500/20 px-1 py-0.5 rounded mx-0.5 border border-rose-500/30 shadow-[0_0_10px_rgba(244,63,94,0.2)]" title="Negative Signal">{part}</span>;
        }
        
        if (isPositive) {
             return <span key={i} className="font-bold text-emerald-300 bg-emerald-500/20 px-1 py-0.5 rounded mx-0.5 border border-emerald-500/30 shadow-[0_0_10px_rgba(16,185,129,0.2)]" title="Positive Signal">{part}</span>;
        }

        return part;
      })}
    </>
  );
};

const ADAPTATION_SEGMENT_CONFIG = {
  "Trapped": { color: "text-rose-400", bg: "bg-rose-500/15", border: "border-rose-500/30", icon: "text-rose-400", description: "Low cushion, future pressure is high" },
  "Wealthy Under Pressure": { color: "text-amber-400", bg: "bg-amber-500/15", border: "border-amber-500/30", icon: "text-amber-400", description: "Financial cushion exists, but future forces are building" },
  "Mature & Stable": { color: "text-blue-400", bg: "bg-blue-500/15", border: "border-blue-500/30", icon: "text-blue-400", description: "Financially steady with lower future pressure" },
  "Future-Ready": { color: "text-emerald-400", bg: "bg-emerald-500/15", border: "border-emerald-500/30", icon: "text-emerald-400", description: "Strong cushion and lower future pressure" },
};

const getAdaptationSegment = (company, faviCutoff = 50, facCutoff = 50) => {
  const fac = Number(company?.fac || 0);
  const favi = Number(company?.favi_score || 0);
  const highFuture = favi >= faviCutoff;
  const highCapacity = fac >= facCutoff;
  if (highFuture && !highCapacity) return "Trapped";
  if (highFuture && highCapacity) return "Wealthy Under Pressure";
  if (!highFuture && !highCapacity) return "Mature & Stable";
  return "Future-Ready";
};

const CompanySummary = ({ data, faviData, observatoryData = null, isAdmin = false, authToken = '' }) => {
  const summaryRef = useRef(null);
  const [copied, setCopied] = useState(false);
  const [pitchCopied, setPitchCopied] = useState(false);
  const [whyWhatCopied, setWhyWhatCopied] = useState(false);
  const [activeTab, setActiveTab] = useState("diagnosis");
  const [buyingInsightMeta, setBuyingInsightMeta] = useState(null);
  const companyDisplayName = data?.companyName || data?.company_name || data?.name || '';

  useEffect(() => {
    if (!isAdmin || !authToken || !companyDisplayName) {
      setBuyingInsightMeta(null);
      return;
    }
    let cancelled = false;
    fetch(`/admin/buying-potential/${encodeURIComponent(companyDisplayName)}`, {
      headers: { Authorization: `Bearer ${authToken}` }
    })
      .then(res => res.ok ? res.json() : null)
      .then(json => {
        if (cancelled) return;
        const meta = json?.exists ? json.metadata : null;
        setBuyingInsightMeta(meta?.html_url ? meta : null);
      })
      .catch(() => {
        if (!cancelled) setBuyingInsightMeta(null);
      });
    return () => { cancelled = true; };
  }, [isAdmin, authToken, companyDisplayName, data?.lastUpdated]);

  
  const isCompanySpecificEvidence = (link) => {
    const claimType = String(link?.claim_type || '').toLowerCase();
    const scope = String(link?.scope || '').toLowerCase();
    return link?.company_specific === true || claimType === 'company_fact' || scope === 'company-specific';
  };
const isForesightContext = (link) => {
    if (link?.company_specific === true || String(link?.scope || '').toLowerCase() === 'company-specific' || String(link?.claim_type || '').toLowerCase() === 'company_fact') return false;
    const claimType = String(link?.claim_type || '').toLowerCase();
    const sourceType = String(link?.source_type || '').toLowerCase();
    const origin = String(link?.origin || '').toLowerCase();
    const scope = String(link?.scope || '').toLowerCase();
    const allowed = (link?.allowed_claim_types || []).map(x => String(x).toLowerCase());
    return origin === 'foresight'
      || scope === 'external-trend'
      || claimType === 'foresight_context'
      || claimType === 'regulatory_context'
      || allowed.includes('foresight_context')
      || allowed.includes('regulatory_context')
      || ['foresight', 'foresight_report', 'trend_report', 'regulation', 'regulatory_context', 'analyst'].includes(sourceType);
  };

  // True when the backend detected a company name in source_says for a non-company-specific
  // record and genericized it. Shows a badge so admins know the rewrite happened.
  const wasSourceSaysGenericized = (link) => link?.source_says_was_genericized === true;


  const normalizedCompanyNames = [data?.company_name, data?.companyName, data?.name]
    .filter(Boolean)
    .flatMap((n) => {
      const base = String(n).replace(/\b(Oyj|Abp|Plc|Ltd|Inc|Corp|Corporation|Group)\b/gi, '').trim();
      return [String(n), base].filter(Boolean);
    });
  const needsSourceRelevanceSplit = (link) => {
    if (!isForesightContext(link) || link?.company_specific === true) return false;
    const ss = String(link?.source_says || '');
    return normalizedCompanyNames.some((n) => n && ss.toLowerCase().includes(String(n).toLowerCase()));
  };
  const getSourceSays = (link) => link?.source_says || link?.snippet || link?.description || '';
  const getPossibleRelevance = (link) => link?.possible_relevance || '';
  const isVerificationConflict = (link) => link?.verification_conflict === true || link?.quote_mismatch === true || link?.claim_irrelevant === true;
  const isEntityVerified = (link) => {
    const contentStatus = String(link?.content_entity_match_status || link?.entity_match_status || '').toLowerCase();
    return link?.entity_verified === true || contentStatus === 'exact' || contentStatus === 'scoped';
  };
  const isClaimSupported = (link) => String(link?.claim_support_status || '').toLowerCase() === 'supported' && !isVerificationConflict(link);
  const isAnalysisVerified = (link) => (
    link?.analysis_verified === true
    && link?.promotion_status === 'generation_eligible'
    && link?.generation_eligible === true
    && isEntityVerified(link)
    && isClaimSupported(link)
  );
  const isWrongEntity = (link) => link?.wrong_entity === true || String(link?.entity_match_status || '').toLowerCase() === 'wrong';
  const isUnknownOrWeakEntity = (link) => {
    const status = String(link?.entity_match_status || '').toLowerCase();
    const scope = String(link?.entity_scope || '').toLowerCase();
    return !isAnalysisVerified(link) && !isWrongEntity(link) && (status === 'weak' || status === 'unknown' || (!!link?.url && !status) || scope === 'unknown');
  };
  const getPromotionLabel = (link) => {
    if (isAnalysisVerified(link)) return 'Verified for analysis';
    if (isVerificationConflict(link)) return 'Blocked: verification conflict';
    if (link?.promotion_status === 'excluded') return 'Excluded';
    if (link?.promotion_status === 'display_only') return 'Display only';
    return '';
  };
  const getVerificationReason = (link) => {
    const reason = String(link?.verification_reason || link?.claim_support_status || '').replace(/_/g, ' ');
    const detail = String(link?.content_verification_reason || '').replace(/_/g, ' ');
    return [reason, detail].filter(Boolean).join(' — ');
  };
  const normalizeCredibility = (cred) => {
    const normalized = String(cred || '').toLowerCase();
    if (['high', 'very_high', 'trusted', 'authoritative'].includes(normalized)) return 'high';
    if (['medium', 'moderate'].includes(normalized)) return 'medium';
    if (['low', 'weak', 'unverified'].includes(normalized)) return 'low';
    return 'unknown';
  };

  const stringifyTrustItem = (item) => {
    if (!item) return '';
    if (typeof item === 'string') return item;
    if (typeof item !== 'object') return String(item);
    if (item.preferred_wording) return item.preferred_wording;
    if (item.warning) return item.warning;
    if (item.message) return item.message;
    if (item.claim) return item.claim;
    if (item.topic) {
        const severity = item.severity ? `${item.severity}: ` : '';
        return `${severity}${item.topic}`;
    }
    try {
        return JSON.stringify(item);
    } catch {
        return '';
    }
  };

  const normalizeTrustList = (...values) => {
    const out = [];
    values.forEach((value) => {
        if (!value) return;
        const items = Array.isArray(value) ? value : [value];
        items.forEach((item) => {
            const text = stringifyTrustItem(item).trim();
            if (text && !out.includes(text)) out.push(text);
        });
    });
    return out;
  };

  const buildEvidenceTrustPool = () => {
    const pool = [];
    const seen = new Set();
    const addLink = (link, scanLabel) => {
        if (!link || typeof link !== 'object') return;
        const url = link.url || link.source_url || '';
        const title = link.title || link.source_title || link.dimension || 'Evidence source';
        const snippet = link.snippet || link.evidence_snippet || link.description || '';
        const key = url
            ? url.toLowerCase().replace(/\/$/, '')
            : `${scanLabel}:${title}:${snippet}`.toLowerCase().slice(0, 240);
        if (!key || seen.has(key)) return;
        seen.add(key);
        pool.push({
            ...link,
            url,
            title,
            snippet,
            _scan: link._scan || scanLabel
        });
    };

    (data.evidence_links || []).forEach((link) => addLink(link, link?.source_type === 'historical' ? 'historical' : 'current'));
    (data.foresight_links || []).forEach((link) => addLink(link, 'foresight'));
    (data.history || []).forEach((h) => {
        const scanDate = h.date ? new Date(h.date).toLocaleDateString('en-US', { month: 'short', year: '2-digit' }) : 'history';
        (h.evidence || []).forEach((link) => addLink(link, scanDate));
        (h.foresight_evidence || []).forEach((link) => addLink(link, `${scanDate} foresight`));
    });

    return pool;
  };

  const evidenceTrustPool = buildEvidenceTrustPool();
  const credibilityCounts = evidenceTrustPool.reduce((acc, link) => {
    const cred = normalizeCredibility(link.source_credibility);
    acc[cred] = (acc[cred] || 0) + 1;
    return acc;
  }, { high: 0, medium: 0, low: 0, unknown: 0 });
  const verifiedSourceCount = evidenceTrustPool.filter((link) => link.verified === true || link.url_verified === true).length;
  const contentReadableCount = evidenceTrustPool.filter((link) => link.content_verified === true).length;
  const entityVerifiedCount = evidenceTrustPool.filter((link) => isEntityVerified(link) && !isWrongEntity(link)).length;
  const claimSupportedCount = evidenceTrustPool.filter((link) => isClaimSupported(link)).length;
  const analysisVerifiedCount = evidenceTrustPool.filter((link) => isAnalysisVerified(link)).length;
  const generationEligibleCount = evidenceTrustPool.filter((link) => isAnalysisVerified(link) && link.generation_eligible === true).length;
  const evidenceSufficiency = typeof data.evidence_sufficiency === 'object' && data.evidence_sufficiency !== null ? data.evidence_sufficiency : null;
  const lowEvidenceLabel = data.low_evidence_label || evidenceSufficiency?.label || '';
  const isLowEvidenceMode = data.low_evidence_mode === true || !!lowEvidenceLabel;
  const displayOnlyCount = evidenceTrustPool.filter((link) => link.promotion_status === 'display_only' || link.claim_support_status === 'unsupported').length;
  const verificationConflictCount = evidenceTrustPool.filter((link) => isVerificationConflict(link)).length;
  const wrongEntityCount = evidenceTrustPool.filter((link) => isWrongEntity(link)).length;
  const unknownEntityCount = evidenceTrustPool.filter((link) => isUnknownOrWeakEntity(link)).length;
  const weakOrUnverifiedSourceCount = evidenceTrustPool.filter((link) => {
    const cred = normalizeCredibility(link.source_credibility);
    const support = String(link.claim_support_status || '').toLowerCase();
    const promotion = String(link.promotion_status || '').toLowerCase();
    return link.verified === false
      || link.url_verified === false
      || cred === 'low'
      || (!!link.url && cred === 'unknown')
      || ['unsupported', 'unchecked', 'unavailable', 'partial'].includes(support)
      || ['display_only', 'excluded'].includes(promotion)
      || isVerificationConflict(link)
      || isWrongEntity(link)
      || isUnknownOrWeakEntity(link);
  }).length;
  const confidenceRaw = data.confidence_score
    ?? data.scan_quality?.confidence_score
    ?? data.scan_quality?.confidence
    ?? data.evidence_sufficiency?.score
    ?? (typeof data.evidence_sufficiency === 'number' ? data.evidence_sufficiency : null);
  const confidenceScore = confidenceRaw != null && !Number.isNaN(Number(confidenceRaw))
    ? Math.round(Number(confidenceRaw))
    : null;
  const confidenceClass = confidenceScore == null
    ? 'text-slate-300'
    : confidenceScore >= 70 ? 'text-emerald-300'
    : confidenceScore >= 45 ? 'text-amber-300'
    : 'text-rose-300';
  const contradictionWarnings = normalizeTrustList(
    data.scan_quality?.contradictions,
    data.scan_quality?.contradiction_warnings,
    data.contradictions,
    data.contradiction_warnings
  );
  const getSummaryLengthWordCount = (note: any) => {
    const match = String(note || '').match(/Summary (?:unusually long|length outside target range) \((\d+) words, expected 325-(?:375|475)\)/i);
    return match ? Number(match[1]) : null;
  };
  const normalizeSummaryLengthNote = (note: any) => {
    const wordCount = getSummaryLengthWordCount(note);
    return wordCount != null && (wordCount < 325 || wordCount > 475)
      ? `Summary length outside target range (${wordCount} words, expected 325-475)`
      : null;
  };
  const rawHallucinationFlags = normalizeTrustList(
    data.hallucination_flags,
    data.ai_verification?.false_claims,
    data.ai_verification?.unsupported_claims,
    data.general_summary_false_claims
  );
  const aiReadinessEvidenceNote = data.ai_readiness_evidence_note
    || data.dimension_evidence_notes?.ai_readiness?.warning
    || '';
  const qualityNotes = normalizeTrustList(
    data.quality_notes,
    data.scan_quality?.quality_notes,
    aiReadinessEvidenceNote,
    rawHallucinationFlags.map(normalizeSummaryLengthNote).filter(Boolean)
  );
  const hallucinationFlags = rawHallucinationFlags.filter((flag) => getSummaryLengthWordCount(flag) == null);
  const wrongEntityWarnings = evidenceTrustPool
    .filter((link) => isWrongEntity(link))
    .map((link) => `${link.title || link.source || link.url || 'Evidence source'}: ${String(link.entity_match_reason || link.verification_reason || 'wrong entity').replace(/_/g, ' ')}`);
  const previousScanCarriedForward = (data.evidence_links || []).some((link) => String(link?.source_type || '').toLowerCase() === 'historical')
    || (data.reality_checklist || []).some((item) => String(item?.source_type || '').toLowerCase() === 'historical');

  // ARCHETYPE LOGIC (Reality vs Financial — unified with dashboard)
  // Prefer the stored archetype from the backend (single source of truth).
  // Fall back to score-based computation for backward compatibility with old data.
  const ARCHETYPE_COLORS = {
      "Thriving": '#10b981',    // Emerald
      "Modernizing": '#22d3ee', // Cyan
      "Cash Cow": '#f59e0b',    // Amber
      "At Risk": '#fb7185',     // Rose/Red
  };

  let gapKey: string;
  if (data.archetype && ARCHETYPE_COLORS[data.archetype]) {
      gapKey = data.archetype;
  } else {
      const fin = data.financial_index ?? data.intent_score ?? 0;
      if (data.reality_score >= 50) {
          gapKey = fin >= 50 ? "Thriving" : "Modernizing";
      } else {
          gapKey = fin >= 50 ? "Cash Cow" : "At Risk";
      }
  }
  const gapLabel = gapKey;
  const gapColor = ARCHETYPE_COLORS[gapKey] || '#94a3b8';

  const ARCHETYPE_DESCRIPTIONS = {
    "Thriving": "High Reality + Strong Financials. Modern operations AND healthy business.",
    "Modernizing": "High Reality + Weak Financials. Strong operations but financial pressure.",
    "Cash Cow": "Low Reality + Strong Financials. Profitable but operationally outdated.",
    "At Risk": "Low Reality + Weak Financials. Both operations and finances under pressure."
  };
  
  let gapValueDisplay = data.reality_gap > 0 ? `+${data.reality_gap}` : `${data.reality_gap}`;

  const radarData = data.intent_breakdown.map(item => ({
    subject: item.name,
    A: item.score,
    fullMark: 100,
  }));

  // Safely access metrics (might be missing in old seed data)
  const metrics = data.metrics || { friction_index: 0, vibrancy_score: 0, total_good_mentions: 0, total_bad_mentions: 0 };
  
  // Friction Logic: 
  const frictionPercentage = Math.round(metrics.friction_index * 100);
  const frictionColor = frictionPercentage > 60 ? 'text-rose-400' : frictionPercentage > 30 ? 'text-amber-400' : 'text-emerald-400';
  const frictionLabel = frictionPercentage > 60 ? 'Battleground' : frictionPercentage > 30 ? 'Conflicted' : 'Coherent';
  
  // Confidence Logic — retained for admin Review Modal but removed from user-facing view.
  // Summary Confidence was misleading: penalty pipeline could show 10% even with 14 good
  // signals, making users distrust solid results. Kept in data for admin triage only.

  // --- CHART DATA PREPARATION ---
  const historyRaw = data.history || [];
  let chartData = [];

  if (historyRaw.length > 0) {
      chartData = historyRaw.map(h => ({
          date: new Date(h.date).toLocaleDateString('en-US', { month: 'short', year: '2-digit' }),
          gap: h.gap
      }));
      chartData.push({
          date: new Date(data.lastUpdated).toLocaleDateString('en-US', { month: 'short', year: '2-digit' }),
          gap: data.reality_gap
      });
  } else {
      const current = new Date(data.lastUpdated);
      const currentGap = data.reality_gap;
      chartData = [];
      for(let i=5; i>=1; i--) {
          const d = new Date(current);
          d.setMonth(d.getMonth() - i);
          const variance = Math.floor(Math.random() * 16) - 8; 
          chartData.push({
              date: d.toLocaleDateString('en-US', { month: 'short', year: '2-digit' }),
              gap: Math.max(-100, Math.min(100, currentGap + variance))
          });
      }
      chartData.push({
          date: current.toLocaleDateString('en-US', { month: 'short', year: '2-digit' }),
          gap: currentGap
      });
  }

  // --- EXPORT LOGIC ---
  const handleCopyJSON = () => {
    const rawData = JSON.stringify(data, null, 2);
    navigator.clipboard.writeText(rawData).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleCopyPitch = () => {
    if (data.pitch_text) {
        navigator.clipboard.writeText(data.pitch_text).then(() => {
            setPitchCopied(true);
            setTimeout(() => setPitchCopied(false), 2000);
        });
    }
  };

  const handleCopyWhyWhat = () => {
    const why = data.why_proposals || [];
    const what = data.what_proposals || [];
    const text = `WHY (Desired Impact):\n1. ${why[0] || ''}\n2. ${why[1] || ''}\n\nWHAT (Desired Effect):\n1. ${what[0] || ''}\n2. ${what[1] || ''}`;
    navigator.clipboard.writeText(text).then(() => {
        setWhyWhatCopied(true);
        setTimeout(() => setWhyWhatCopied(false), 2000);
    });
  };

  const handleDownloadHTML = () => {
    var buildAndDownload = function(exportData) {
        var companyName = exportData.companyName || exportData.company_name || exportData.name || 'Company';
        var generatedDate = new Date().toISOString().split('T')[0];
        var rawData = JSON.stringify(exportData, null, 2).replace(/<\/script/gi, '<\\/script');
        var escapeHTML = function(value) {
            return String(value == null ? '' : value)
                .replace(/&/g, '&amp;')
                .replace(/\x3c/g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        };
        var renderInlineMarkdown = function(value) {
            return escapeHTML(value).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        };
        var hasText = function(value) {
            return typeof value === 'string' && value.trim().length > 0;
        };
        var renderParagraphs = function(text) {
            if (!hasText(text)) return '';
            return String(text).split(/\n\n+/).map(function(block) {
                var trimmed = block.trim();
                if (!trimmed) return '';
                return '<p>' + renderInlineMarkdown(trimmed).replace(/\n/g, '<br>') + '</p>';
            }).join('');
        };
        var renderReportBlocks = function(text) {
            if (!hasText(text)) return '';
            return String(text).split(/\n\n+/).map(function(block) {
                var trimmed = block.trim();
                if (!trimmed) return '';
                var h2Match = trimmed.match(/^##\s+(.+)/);
                if (h2Match) return '<h3>' + renderInlineMarkdown(h2Match[1]) + '</h3>';
                var h3Match = trimmed.match(/^###\s+(.+)/);
                if (h3Match) return '<h4>' + renderInlineMarkdown(h3Match[1]) + '</h4>';
                if (/^-{3,}$/.test(trimmed)) return '<hr>';
                return '<p>' + renderInlineMarkdown(trimmed).replace(/\n/g, '<br>') + '</p>';
            }).join('');
        };
        var renderList = function(items) {
            if (!Array.isArray(items) || items.length === 0) return '';
            return '<ul>' + items.filter(Boolean).map(function(item) {
                return '<li>' + renderInlineMarkdown(typeof item === 'string' ? item : JSON.stringify(item)) + '</li>';
            }).join('') + '</ul>';
        };
        var renderSection = function(title, eyebrow, bodyHTML, modifier) {
            if (!bodyHTML) return '';
            var label = eyebrow ? '<div class="eyebrow">' + escapeHTML(eyebrow) + '</div>' : '';
            return '<section class="section ' + (modifier || '') + '">' + label + '<h2>' + escapeHTML(title) + '</h2>' + bodyHTML + '</section>';
        };
        var renderMetric = function(label, value, hint) {
            return '<div class="metric"><span>' + escapeHTML(label) + '</span><strong>' + escapeHTML(value) + '</strong>' + (hint ? '<em>' + escapeHTML(hint) + '</em>' : '') + '</div>';
        };
        var formatScore = function(value, suffix) {
            if (value === null || value === undefined || value === '') return 'n/a';
            var num = Number(value);
            if (!Number.isFinite(num)) return String(value);
            return Math.round(num).toString() + (suffix || '');
        };
        var stringifyItem = function(item) {
            if (!item) return '';
            if (typeof item === 'string') return item;
            if (typeof item !== 'object') return String(item);
            return item.preferred_wording || item.warning || item.message || item.claim || item.topic || JSON.stringify(item);
        };
        var normalizeList = function() {
            var out = [];
            Array.prototype.slice.call(arguments).forEach(function(value) {
                if (!value) return;
                (Array.isArray(value) ? value : [value]).forEach(function(item) {
                    var text = stringifyItem(item).trim();
                    if (text && out.indexOf(text) === -1) out.push(text);
                });
            });
            return out;
        };
        var normalizeCred = function(cred) {
            var c = String(cred || '').toLowerCase();
            if (['high', 'very_high', 'trusted', 'authoritative'].indexOf(c) >= 0) return 'high';
            if (['medium', 'moderate'].indexOf(c) >= 0) return 'medium';
            if (['low', 'weak', 'unverified'].indexOf(c) >= 0) return 'low';
            return 'unknown';
        };
        var evidencePool = []
            .concat(Array.isArray(exportData.evidence_links) ? exportData.evidence_links : [])
            .concat(Array.isArray(exportData.foresight_links) ? exportData.foresight_links : []);
        var credibility = evidencePool.reduce(function(acc, link) {
            var cred = normalizeCred(link && (link.source_credibility || link.credibility));
            acc[cred] = (acc[cred] || 0) + 1;
            return acc;
        }, { high: 0, medium: 0, low: 0, unknown: 0 });
        var verifiedSources = evidencePool.filter(function(link) {
            return link && (link.verified === true || link.url_verified === true);
        }).length;
        var isVerificationConflict = function(link) {
            return link && (link.verification_conflict === true || link.quote_mismatch === true || link.claim_irrelevant === true);
        };
        var isEntitySafe = function(link) {
            var status = String(link && (link.content_entity_match_status || link.entity_match_status) || '').toLowerCase();
            return link && (link.entity_verified === true || status === 'exact' || status === 'scoped');
        };
        var isClaimSupported = function(link) {
            return link && String(link.claim_support_status || '').toLowerCase() === 'supported' && !isVerificationConflict(link);
        };
        var isAnalysisSafe = function(link) {
            return link && link.analysis_verified === true && link.promotion_status === 'generation_eligible' && link.generation_eligible === true && isEntitySafe(link) && isClaimSupported(link);
        };
        var isWrongEntity = function(link) {
            return link && (link.wrong_entity === true || String(link.entity_match_status || '').toLowerCase() === 'wrong');
        };
        var isUnknownEntity = function(link) {
            var status = String(link && link.entity_match_status || '').toLowerCase();
            var scope = String(link && link.entity_scope || '').toLowerCase();
            return link && !isAnalysisSafe(link) && !isWrongEntity(link) && (status === 'weak' || status === 'unknown' || (!!(link.url || link.source_url) && !status) || scope === 'unknown');
        };
        var analysisVerifiedSources = evidencePool.filter(function(link) {
            return isAnalysisSafe(link);
        }).length;
        var contentReadableSources = evidencePool.filter(function(link) { return link && link.content_verified === true; }).length;
        var entityVerifiedSources = evidencePool.filter(function(link) { return isEntitySafe(link) && !isWrongEntity(link); }).length;
        var claimSupportedSources = evidencePool.filter(isClaimSupported).length;
        var generationEligibleSources = evidencePool.filter(function(link) { return isAnalysisSafe(link) && link.generation_eligible === true; }).length;
        var evidenceSufficiency = exportData.evidence_sufficiency && typeof exportData.evidence_sufficiency === 'object' ? exportData.evidence_sufficiency : null;
        var lowEvidenceLabel = exportData.low_evidence_label || (evidenceSufficiency && evidenceSufficiency.label) || '';
        var isLowEvidenceMode = exportData.low_evidence_mode === true || !!lowEvidenceLabel;
        var verificationConflictSources = evidencePool.filter(isVerificationConflict).length;
        var displayOnlySources = evidencePool.filter(function(link) {
            return link && (link.promotion_status === 'display_only' || link.claim_support_status === 'unsupported');
        }).length;
        var wrongEntitySources = evidencePool.filter(isWrongEntity).length;
        var unknownEntitySources = evidencePool.filter(isUnknownEntity).length;
        var weakSources = evidencePool.filter(function(link) {
            var cred = normalizeCred(link && (link.source_credibility || link.credibility));
            var support = String(link && link.claim_support_status || '').toLowerCase();
            var promotion = String(link && link.promotion_status || '').toLowerCase();
            return link && (
                link.verified === false
                || link.url_verified === false
                || cred === 'low'
                || ((link.url || link.source_url) && cred === 'unknown')
                || ['unsupported', 'unchecked', 'unavailable', 'partial'].indexOf(support) >= 0
                || ['display_only', 'excluded'].indexOf(promotion) >= 0
                || isVerificationConflict(link)
                || isWrongEntity(link)
                || isUnknownEntity(link)
            );
        }).length;
        var carriedForward = evidencePool.some(function(link) {
            return String(link && link.source_type || '').toLowerCase() === 'historical';
        }) || (exportData.reality_checklist || []).some(function(item) {
            return String(item && item.source_type || '').toLowerCase() === 'historical';
        });
        var contradictionWarnings = normalizeList(
            exportData.scan_quality && exportData.scan_quality.contradictions,
            exportData.scan_quality && exportData.scan_quality.contradiction_warnings,
            exportData.contradictions,
            exportData.contradiction_warnings
        );
        var hallucinationWarnings = normalizeList(
            exportData.hallucination_flags,
            exportData.ai_verification && exportData.ai_verification.false_claims,
            exportData.ai_verification && exportData.ai_verification.unsupported_claims,
            exportData.general_summary_false_claims
        );
        var getSummaryLengthWordCount = function(note) {
            var match = String(note || '').match(/Summary (?:unusually long|length outside target range) \((\d+) words, expected 325-(?:375|475)\)/i);
            return match ? Number(match[1]) : null;
        };
        var normalizeSummaryLengthNote = function(note) {
            var wordCount = getSummaryLengthWordCount(note);
            return wordCount !== null && (wordCount < 325 || wordCount > 475)
                ? 'Summary length outside target range (' + wordCount + ' words, expected 325-475)'
                : null;
        };
        var qualityNotes = normalizeList(
            exportData.quality_notes,
            exportData.scan_quality && exportData.scan_quality.quality_notes,
            exportData.ai_readiness_evidence_note,
            exportData.dimension_evidence_notes && exportData.dimension_evidence_notes.ai_readiness && exportData.dimension_evidence_notes.ai_readiness.warning,
            hallucinationWarnings.map(normalizeSummaryLengthNote).filter(Boolean)
        );
        hallucinationWarnings = hallucinationWarnings.filter(function(note) {
            return getSummaryLengthWordCount(note) === null;
        });
        var wrongEntityWarnings = evidencePool.filter(isWrongEntity).map(function(link) {
            return (link.title || link.source || link.url || 'Evidence source') + ': ' + String(link.entity_match_reason || link.verification_reason || 'wrong entity').replace(/_/g, ' ');
        });
        var gap = Number(exportData.intent_score || 0) - Number(exportData.reality_score || 0);
        var metrics = [
            renderMetric('Ambition Score', formatScore(exportData.intent_score, '/100'), 'Narrative clarity'),
            renderMetric('Reality Score', formatScore(exportData.reality_score, '/100'), 'Evidence check'),
            renderMetric('Integrity Delta', (gap > 0 ? '+' : '') + Math.round(gap), 'Ambition minus reality'),
            renderMetric('Confidence', formatScore(exportData.confidence_score ?? exportData.scan_quality?.confidence_score ?? exportData.evidence_sufficiency?.score, '%'), 'Model confidence'),
            renderMetric('Financial Health', formatScore(exportData.financial_index, '/100'), 'Adaptation capacity'),
            renderMetric('URL Verified', verifiedSources, weakSources + ' weak/unverified'),
            renderMetric('Content Readable', contentReadableSources, 'readable page text'),
            renderMetric('Target Entity Verified', entityVerifiedSources, 'right company/scope'),
            renderMetric('Claim Supported', claimSupportedSources, 'source backs claim'),
            renderMetric('Generation Eligible', generationEligibleSources, 'safe for prose'),
            renderMetric('Source Register', isLowEvidenceMode ? (lowEvidenceLabel || 'Low evidence') : 'Complete', isLowEvidenceMode ? 'cautious prose mode' : 'promoted evidence available'),
            renderMetric('Display / Unsupported', displayOnlySources, 'audit only'),
            renderMetric('Wrong Entity', wrongEntitySources, 'blocked sources'),
            renderMetric('Unknown Entity', unknownEntitySources, 'not promoted'),
            renderMetric('Verification Conflict', verificationConflictSources, 'blocked conflicts'),
            renderMetric('Evidence Links', evidencePool.length, 'Company and foresight sources'),
            renderMetric('Washing Flag', exportData.washing_flag ? 'Yes' : 'No', exportData.washing_type || 'Narrative vs evidence'),
            renderMetric('Carried Forward', carriedForward ? 'Yes' : 'No', 'Previous-scan evidence')
        ];
        if (faviData && faviData.segment) {
            var faviCutoff = faviData.favi_cutoff ?? faviData.favi_cutoffs?.favi ?? 50;
            var facCutoff = faviData.fac_cutoff ?? 50;
            metrics.push(renderMetric('Adaptation Segment', getAdaptationSegment(faviData, faviCutoff, facCutoff), 'FAC x FAVI'));
            metrics.push(renderMetric('FAVI', formatScore(faviData.favi_score, '/100'), 'Foresight vulnerability'));
            metrics.push(renderMetric('Foresight Pressure', formatScore(faviData.fps, '/100'), 'FPS'));
            metrics.push(renderMetric('Readiness Gap', formatScore(faviData.readiness_gap, '/100'), faviData.readiness_gap_source || 'Current x future gap'));
            metrics.push(renderMetric('Adaptation Capacity', formatScore(faviData.fac, '/100'), 'FAC'));
            metrics.push(renderMetric('Current Vulnerability', formatScore(faviData.current_vulnerability ?? faviData.current_vulnerability_signal, '/100'), 'Portfolio-relative'));
        }
        var headerMeta = [
            exportData.domain ? '<span>' + escapeHTML(exportData.domain) + '</span>' : '',
            exportData.archetype ? '<span>' + escapeHTML(exportData.archetype) + '</span>' : '',
            exportData.lastUpdated ? '<span>Scanned ' + escapeHTML(new Date(exportData.lastUpdated).toLocaleDateString('en-FI', { day: 'numeric', month: 'long', year: 'numeric' })) + '</span>' : '',
            '<span>Generated ' + escapeHTML(generatedDate) + '</span>'
        ].filter(Boolean).join('');
        var diagnosisHTML = renderParagraphs(exportData.summary_text)
            + (Array.isArray(exportData.evidence_based_risks) && exportData.evidence_based_risks.length > 0
                ? '<div class="callout"><h3>Evidence-Based Risks</h3>' + renderList(exportData.evidence_based_risks) + '</div>'
                : '');
        var trustHTML = '<div class="metrics">'
            + renderMetric('URL Verified', verifiedSources, 'reachable source links')
            + renderMetric('Content Readable', contentReadableSources, 'readable page text')
            + renderMetric('Target Entity Verified', entityVerifiedSources, 'right company or explicit scope')
            + renderMetric('Claim Supported', claimSupportedSources, 'source content backs claim')
            + renderMetric('Generation Eligible', generationEligibleSources, 'safe for prose')
            + renderMetric('Source Register', isLowEvidenceMode ? (lowEvidenceLabel || 'Low evidence') : 'Complete', isLowEvidenceMode ? 'cautious prose mode' : 'promoted evidence available')
            + renderMetric('Display / Unsupported', displayOnlySources, 'audit only')
            + renderMetric('Wrong Entity', wrongEntitySources, 'blocked sources')
            + renderMetric('Unknown Entity', unknownEntitySources, 'not promoted')
            + renderMetric('Verification Conflict', verificationConflictSources, 'blocked conflicts')
            + renderMetric('Weak / Unverified', weakSources, 'unsupported, weak, unknown, or failed')
            + renderMetric('Confidence', formatScore(exportData.confidence_score ?? exportData.scan_quality?.confidence_score ?? exportData.evidence_sufficiency?.score, '%'), 'evidence confidence')
            + renderMetric('Credibility Mix', 'H' + credibility.high + ' M' + credibility.medium + ' L' + credibility.low + ' U' + credibility.unknown, 'high/medium/low/unknown')
            + renderMetric('Contradictions', contradictionWarnings.length || 'None', 'stored warnings')
            + renderMetric('Unsupported Flags', hallucinationWarnings.length || 'None', 'hallucination checks')
            + renderMetric('Quality Notes', qualityNotes.length || 'None', 'format/style checks')
            + '</div>'
            + (isLowEvidenceMode ? '<div class="callout amber"><h3>' + escapeHTML(lowEvidenceLabel || 'Low promoted evidence') + '</h3><p>Promoted source-register evidence is limited. The report may still use verified structured financial findings and internal scanner observations, but precise external company claims require source-backed support.</p></div>' : '')
            + (contradictionWarnings.length ? '<div class="callout amber"><h3>Contradiction Warnings</h3>' + renderList(contradictionWarnings) + '</div>' : '<p class="muted">No contradiction warnings detected.</p>')
            + (wrongEntityWarnings.length ? '<div class="callout"><h3>Wrong-Entity Warnings</h3>' + renderList(wrongEntityWarnings) + '</div>' : '<p class="muted">No wrong-entity evidence detected.</p>')
            + (hallucinationWarnings.length ? '<div class="callout"><h3>Hallucination / Unsupported-Claim Flags</h3>' + renderList(hallucinationWarnings) + '</div>' : '<p class="muted">No hallucination or unsupported-claim flags detected.</p>')
            + (qualityNotes.length ? '<div class="callout cyan"><h3>Quality Notes</h3>' + renderList(qualityNotes) + '</div>' : '<p class="muted">No format or style notes detected.</p>');
        var sourceTable = evidencePool.length ? '<div class="table-wrap"><table><thead><tr><th>Source</th><th>Type</th><th>Cred</th><th>URL</th><th>Promotion</th><th>Entity</th><th>Support</th><th>Model</th><th>Quote / Reason</th></tr></thead><tbody>'
            + evidencePool.map(function(link) {
                var url = link.url || link.source_url || '';
                var sourceName = link.title || link.source || link.source_title || link.fp_theme || 'Evidence source';
                var quote = link.source_quote || link.source_says || link.snippet || link.description || link.summary || '';
                var analysis = isAnalysisSafe(link) ? 'Verified for analysis' : (isVerificationConflict(link) ? 'Blocked: verification conflict' : (link.promotion_status || link.verification_reason || link.entity_match_reason || 'Display only'));
                var entity = link.entity_verified === true ? 'verified' : (link.content_entity_match_status || link.entity_match_status || 'unknown');
                var reason = [quote, link.content_verification_reason, link.content_entity_match_reason].filter(Boolean).join(' — ');
                return '<tr><td>' + escapeHTML(sourceName) + '</td><td>' + escapeHTML(link.source_type || link.origin || '') + '</td><td>' + escapeHTML(link.source_credibility || link.credibility || 'unknown') + '</td><td>' + (url ? '<a href="' + escapeHTML(url) + '">' + escapeHTML(url) + '</a>' : '') + '</td><td>' + escapeHTML(analysis) + '</td><td>' + escapeHTML(entity) + '</td><td>' + escapeHTML(link.claim_support_status || '') + '</td><td>' + escapeHTML(link.verification_model || '') + '</td><td>' + escapeHTML(String(reason).slice(0, 520)) + '</td></tr>';
            }).join('') + '</tbody></table></div>' : '<p class="muted">No evidence links were available in this export.</p>';
        function checklistMarkerState(item) {
            var source = String(item.source_type || item.source || '').toLowerCase();
            var rawStatus = String(item.status || '').trim();
            var normalizedStatus = rawStatus.toLowerCase().replace(/[_-]/g, ' ');
            if (source === 'historical') return 'Carried forward';
            if (rawStatus && normalizedStatus !== 'not verified' && normalizedStatus !== 'unverified') {
                return rawStatus;
            }
            if (item.found === true || item.passed === true) return 'Detected';
            if (item.found === false || item.passed === false) return 'Not detected in scan';
            return 'Needs review';
        }
        var checklistHTML = Array.isArray(exportData.reality_checklist) && exportData.reality_checklist.length > 0
            ? '<div class="table-wrap"><table><thead><tr><th>Finding</th><th>Category</th><th>Marker State</th><th>Source</th></tr></thead><tbody>'
                + exportData.reality_checklist.map(function(item) {
                    var finding = item.query || item.name || item.title || item.finding || item.category || 'Reality marker';
                    return '<tr><td>' + escapeHTML(finding) + '</td><td>' + escapeHTML(item.category || '') + '</td><td>' + escapeHTML(checklistMarkerState(item)) + '</td><td>' + escapeHTML(item.source_type || item.source || '') + '</td></tr>';
                }).join('') + '</tbody></table></div>' : '';
        var breakdownHTML = Array.isArray(exportData.intent_breakdown) && exportData.intent_breakdown.length > 0
            ? '<p class="muted">Dimension scores below describe Scanner 1 ambition / intent signals. They are not standalone proof of verified operational capability.</p><div class="table-wrap"><table><thead><tr><th>Dimension</th><th>Ambition / Intent Score</th></tr></thead><tbody>'
                + exportData.intent_breakdown.map(function(item) {
                    return '<tr><td>' + escapeHTML(item.name) + '</td><td>' + escapeHTML(item.score) + '/100</td></tr>';
                }).join('') + '</tbody></table></div>' : '';
        var domainDossierHTML = '';
        if (exportData.domain && observatoryData && observatoryData.domain_dossiers) {
            var dossier = observatoryData.domain_dossiers.find(function(d) { return d.domain === exportData.domain; });
            if (dossier && dossier.text) {
                domainDossierHTML = '<div class="callout cyan"><h3>Scanner Portfolio Context</h3><p>These domain metrics are derived from the Scanner’s analyzed company portfolio. They summarize observed company statuses and foresight pressure inside the Scanner dataset, rather than claiming to be a complete external market census.</p></div>'
                    + renderReportBlocks(String(dossier.text).replace(/^#\s+Domain Force Dossier.*$/im, '').trim());
            }
        }
        var renderSourceLink = function(url, label) {
            if (!url) return '';
            return '<a href="' + escapeHTML(url) + '">' + escapeHTML(label || url) + '</a>';
        };
        var renderStrategicForesightEvidence = function(sf) {
            if (!sf || typeof sf !== 'object') return '';
            var parts = [];
            var validation = sf.validation_summary || {};
            var hasValidation = Object.keys(validation).length > 0 || sf.insufficient_evidence;
            var forces = Array.isArray(sf.dimension_forces) ? sf.dimension_forces : [];
            if (hasValidation) {
                var validationMetrics = [
                    renderMetric('Quality', validation.quality || (sf.insufficient_evidence ? 'Insufficient' : 'n/a'), 'validation gate'),
                    renderMetric('Total Forces', validation.total_forces ?? forces.length, 'detected signals'),
                    renderMetric('Validated Forces', validation.validated_forces ?? 0, 'passed checks'),
                    renderMetric('Verified URLs', validation.total_verified_urls ?? 0, 'reachable evidence'),
                    renderMetric('Flagged Forces', validation.flagged_forces ?? 0, 'needs caution')
                ];
                parts.push('<div class="metrics">' + validationMetrics.join('') + '</div>');
                if (sf.insufficient_evidence) {
                    parts.push('<div class="callout amber"><h3>Validation Note</h3><p>Strategic foresight evidence was marked as insufficient or limited for this scan.</p></div>');
                }
            }
            if (forces.length) {
                parts.push('<h3>Key Forces</h3><div class="table-wrap"><table><thead><tr><th>Force</th><th>Dimension / Theme</th><th>Timing</th><th>Confidence</th><th>Credibility</th><th>Verification</th><th>Company Impact</th><th>Source</th></tr></thead><tbody>'
                    + forces.map(function(force) {
                        var verification = [
                            force.url_verified === true ? 'URL verified' : (force.url_verified === false ? 'URL not verified' : ''),
                            force.analysis_verified === true ? 'Analysis verified' : '',
                            force.promotion_status || force.claim_support_status || ''
                        ].filter(Boolean).join(' / ');
                        var sourceLinks = [];
                        if (force.source_url) sourceLinks.push(renderSourceLink(force.source_url, 'Primary source'));
                        if (Array.isArray(force.verified_supporting_sources)) {
                            force.verified_supporting_sources.slice(0, 3).forEach(function(src, idx) {
                                var srcUrl = typeof src === 'string' ? src : (src && (src.url || src.source_url));
                                if (srcUrl && sourceLinks.indexOf(srcUrl) === -1) {
                                    sourceLinks.push(renderSourceLink(srcUrl, 'Supporting source ' + (idx + 1)));
                                }
                            });
                        }
                        return '<tr><td>' + escapeHTML(force.type || force.trend || force.force || 'Foresight force') + '</td><td>' + escapeHTML([force.kb_dimension, force.fp_theme].filter(Boolean).join(' / ')) + '</td><td>' + escapeHTML(force.timeframe || '') + '</td><td>' + escapeHTML(force.confidence || '') + '</td><td>' + escapeHTML(force.source_credibility || force.credibility || 'unknown') + '</td><td>' + escapeHTML(verification || 'Not recorded') + '</td><td>' + escapeHTML(force.impact_on_company || force.possible_relevance || force.description || '') + '</td><td>' + (sourceLinks.join('<br>') || '') + '</td></tr>';
                    }).join('') + '</tbody></table></div>');
            }
            if (hasText(sf.cross_impact_chain)) {
                var chainNodes = String(sf.cross_impact_chain).split(/\s*(?:→|->)\s*/).map(function(node) { return node.trim(); }).filter(Boolean);
                parts.push('<h3>Cross-Impact Chain</h3>' + (chainNodes.length > 1
                    ? '<ol>' + chainNodes.map(function(node) { return '<li>' + renderInlineMarkdown(node) + '</li>'; }).join('') + '</ol>'
                    : renderParagraphs(sf.cross_impact_chain)));
            }
            var scenarios = sf.scenarios || {};
            var scenarioRows = [
                ['If They Act Now', scenarios.acceleration],
                ['Continue Unchanged', scenarios.status_quo],
                ['If Disrupted', scenarios.disruption]
            ].filter(function(row) { return hasText(row[1]); });
            if (scenarioRows.length) {
                parts.push('<h3>Micro-Scenarios</h3><div class="table-wrap"><table><thead><tr><th>Scenario</th><th>Implication</th></tr></thead><tbody>'
                    + scenarioRows.map(function(row) {
                        return '<tr><td>' + escapeHTML(row[0]) + '</td><td>' + renderInlineMarkdown(row[1]) + '</td></tr>';
                    }).join('') + '</tbody></table></div>');
            }
            var climate = sf.climate_exposure || {};
            if (hasText(climate.headline) || hasText(climate.physical_risk) || hasText(climate.transition_risk) || hasText(climate.risk_level)) {
                parts.push('<h3>Climate Exposure</h3><div class="metrics">'
                    + renderMetric('Risk Level', climate.risk_level || 'n/a', 'climate exposure')
                    + '</div>'
                    + (hasText(climate.headline) ? '<p>' + renderInlineMarkdown(climate.headline) + '</p>' : '')
                    + (hasText(climate.physical_risk) ? '<h4>Physical Risk</h4><p>' + renderInlineMarkdown(climate.physical_risk) + '</p>' : '')
                    + (hasText(climate.transition_risk) ? '<h4>Transition Risk</h4><p>' + renderInlineMarkdown(climate.transition_risk) + '</p>' : ''));
            }
            return parts.join('');
        };
        var activationHTML = '<div class="two-col"><div><h3>WHY</h3>' + renderList(exportData.why_proposals) + '</div><div><h3>WHAT</h3>' + renderList(exportData.what_proposals) + '</div></div>'
            + renderSection('Strategic Pitch', 'Outbound narrative', renderParagraphs(exportData.pitch_text), 'nested');
        var strategicForesightData = exportData.strategic_foresight || {};
        var strategicForesightHTML = renderStrategicForesightEvidence(strategicForesightData);
        var rawDataHTML = '<details><summary>Open complete raw JSON payload</summary><pre>' + escapeHTML(JSON.stringify(exportData, null, 2)) + '</pre></details>';
        var htmlContent = '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
            + '<meta charset="UTF-8">\n'
            + '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
            + '<title>' + escapeHTML(companyName) + ' - Master Data Report</title>\n'
            + '<style>\n'
            + ':root{color-scheme:dark;--bg:#0f172a;--panel:#172033;--panel2:#111827;--line:#334155;--text:#e5e7eb;--muted:#94a3b8;--cyan:#67e8f9;--green:#6ee7b7;--amber:#fbbf24;--rose:#fda4af;--violet:#c4b5fd;}\n'
            + '*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,Arial,sans-serif;line-height:1.55}main{max-width:1180px;margin:0 auto;padding:40px 28px 56px}.hero{border:1px solid var(--line);background:linear-gradient(135deg,#172033,#0f172a);border-radius:8px;padding:30px;margin-bottom:20px}.kicker{color:var(--cyan);font-size:11px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}.hero h1{margin:8px 0 10px;font-size:34px;line-height:1.1}.meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}.meta span,.chips span{border:1px solid rgba(148,163,184,.28);background:rgba(15,23,42,.62);border-radius:999px;padding:5px 9px;color:var(--muted);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}.metric{border:1px solid rgba(148,163,184,.22);background:rgba(15,23,42,.72);border-radius:8px;padding:14px}.metric span{display:block;color:var(--muted);font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.1em}.metric strong{display:block;margin-top:4px;font-size:23px;color:white}.metric em{display:block;margin-top:3px;color:var(--muted);font-size:11px;font-style:normal}.section{border:1px solid rgba(148,163,184,.22);background:rgba(17,24,39,.78);border-radius:8px;padding:24px;margin-top:16px}.section.nested{background:rgba(15,23,42,.38);padding:18px}.section h2{margin:0 0 14px;color:white;font-size:20px}.section h3{margin:20px 0 8px;color:var(--cyan);font-size:14px;text-transform:uppercase;letter-spacing:.08em}.section h4{margin:16px 0 6px;color:white;font-size:14px}.section p{margin:0 0 13px;color:#dbe4f0}.eyebrow{margin-bottom:6px;color:var(--muted);font-size:10px;font-weight:800;letter-spacing:.14em;text-transform:uppercase}.two-col{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}.two-col h3{color:var(--amber)}ul{margin:0;padding-left:20px}li{margin:8px 0}.callout{margin-top:18px;border-left:3px solid var(--rose);background:rgba(244,63,94,.08);padding:14px 16px}.callout.amber{border-left-color:var(--amber);background:rgba(251,191,36,.08)}.callout.cyan{border-left-color:var(--cyan);background:rgba(34,211,238,.08)}.stakeholders{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}.stakeholders article{border:1px solid rgba(148,163,184,.18);background:rgba(15,23,42,.55);border-radius:8px;padding:15px}.stakeholders h3{margin:0;color:white;text-transform:none;letter-spacing:0;font-size:15px}.role{color:var(--green);font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;margin:4px 0 8px}.stakeholders small{display:block;margin-top:10px;color:var(--muted);font-size:11px}.table-wrap{overflow-x:auto;margin-top:10px;margin-bottom:18px;border:1px solid rgba(148,163,184,.18);border-radius:8px}table{width:100%;border-collapse:collapse;font-size:11px;min-width:820px}th,td{padding:8px 10px;text-align:left;border-bottom:1px solid rgba(148,163,184,.14);vertical-align:top}th{color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.08em;background:rgba(15,23,42,.68)}td{color:#dbe4f0}a{color:var(--cyan);word-break:break-all}.muted{color:var(--muted)!important}strong{color:white}hr{border:0;border-top:1px solid var(--line);margin:20px 0}details{border:1px solid rgba(148,163,184,.22);border-radius:8px;background:rgba(15,23,42,.72);padding:14px}summary{cursor:pointer;color:var(--cyan);font-weight:800}pre{white-space:pre-wrap;word-break:break-word;color:#cbd5e1;font-size:11px;line-height:1.45;max-height:720px;overflow:auto}.footer{margin-top:28px;color:#64748b;font-size:11px;text-align:center}.page-note{color:var(--muted);max-width:850px;margin:0}.scanner-link{display:inline-block;border:1px solid rgba(103,232,249,.38);background:rgba(8,145,178,.18);color:var(--cyan);border-radius:8px;padding:10px 14px;font-weight:800;text-decoration:none}\n'
            + '</style>\n</head>\n<body>\n<main>\n'
            + '<header class="hero"><div class="kicker">Master Data HTML Export</div><h1>' + escapeHTML(companyName) + '</h1><p class="page-note">Complete uploadable evidence and analysis package. This file includes collected sources, trust metadata, diagnosis, activation context, foresight analysis, detailed forensic report, and the raw JSON payload.</p><div class="meta">' + headerMeta + '</div></header>\n'
            + renderSection('General Summary', 'Executive context', renderParagraphs(exportData.general_summary))
            + renderSection('AI Diagnosis', 'Interpretation and risks', diagnosisHTML)
            + renderSection('Master Metrics', 'All score and trust signals', '<div class="metrics">' + metrics.join('') + '</div>' + breakdownHTML)
            + renderSection('Evidence Trust', 'Source verification and promotion state', trustHTML)
            + renderSection('Strategic Activation', 'WHY, WHAT, and pitch', activationHTML)
            + renderSection('Strategic Foresight Evidence', 'Key forces, cross-impact, scenarios, and climate exposure', strategicForesightHTML)
            + renderSection('Reality Checklist', 'Forensic reality markers', checklistHTML)
            + renderSection('Source Evidence Register', 'All company and foresight sources', sourceTable)
            + renderSection(exportData.domain ? 'Domain Force Dossier: ' + exportData.domain : 'Domain Force Dossier', 'Market context', domainDossierHTML)
            + renderSection('Detailed Forensic Report', 'Diagnostic detail', renderReportBlocks(exportData.detailed_report))
            + renderSection('Raw JSON Payload', 'Machine-readable master data', rawDataHTML)
            + '<section class="section"><div class="eyebrow">Interactive source</div><h2>See Detailed Report</h2><p class="muted">For the full interactive scanner view, open the Scanner.</p><p><a class="scanner-link" href="https://the-demand-scanner.up.railway.app/" target="_blank" rel="noopener noreferrer">See detailed report - Scanner</a></p></section>'
            + '<script id="forensic-data" type="application/json">\n' + rawData + '\n<' + '/script>\n'
            + '<div class="footer">AdaptabilityScanner | Master data export | Complete raw data embedded in #forensic-data</div>\n'
            + '</main>\n</body>\n</html>';
        var blob = new Blob([htmlContent], { type: 'text/html' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = 'Master_Data_Report_' + String(companyName).replace(/\s+/g, '_') + '_' + generatedDate + '.html';
        a.click();
        URL.revokeObjectURL(url);
    };

    fetch('/company/' + encodeURIComponent(data.companyName) + '?t=' + Date.now())
        .then(function(res) {
            if (res.ok) return res.json();
            console.warn('[Master Data HTML Export] Fetch response not ok:', res.status);
            return null;
        })
        .catch(function(err) {
            console.warn('[Master Data HTML Export] Fetch error:', err);
            return null;
        })
        .then(function(fresh) {
            var exportData = fresh ? Object.assign({}, data, fresh) : data;
            buildAndDownload(exportData);
        });
  };

  const handleDownloadExecutiveHTML = () => {
    const escapeHTML = function(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/\x3c/g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    };

    const renderInlineMarkdown = function(value) {
        return escapeHTML(value).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    };

    const hasText = function(value) {
        return typeof value === 'string' && value.trim().length > 0;
    };

    const renderParagraphs = function(text) {
        if (!hasText(text)) return '';
        return String(text).split(/\n\n+/).map(function(block) {
            const trimmed = block.trim();
            if (!trimmed) return '';
            return '<p>' + renderInlineMarkdown(trimmed).replace(/\n/g, '<br>') + '</p>';
        }).join('');
    };

    const renderReportBlocks = function(text) {
        if (!hasText(text)) return '';
        return String(text).split(/\n\n+/).map(function(block) {
            const trimmed = block.trim();
            if (!trimmed) return '';
            const h2Match = trimmed.match(/^##\s+(.+)/);
            if (h2Match) return '<h3>' + renderInlineMarkdown(h2Match[1]) + '</h3>';
            const h3Match = trimmed.match(/^###\s+(.+)/);
            if (h3Match) return '<h4>' + renderInlineMarkdown(h3Match[1]) + '</h4>';
            if (/^-{3,}$/.test(trimmed)) return '<hr>';
            return '<p>' + renderInlineMarkdown(trimmed).replace(/\n/g, '<br>') + '</p>';
        }).join('');
    };

    const renderList = function(items) {
        if (!Array.isArray(items) || items.length === 0) return '';
        return '<ul>' + items.filter(Boolean).map(function(item) {
            return '<li>' + renderInlineMarkdown(item) + '</li>';
        }).join('') + '</ul>';
    };

    const renderSection = function(title, eyebrow, bodyHTML, modifier) {
        if (!bodyHTML) return '';
        const label = eyebrow ? '<div class="eyebrow">' + escapeHTML(eyebrow) + '</div>' : '';
        return '<section class="section ' + (modifier || '') + '">' + label + '<h2>' + escapeHTML(title) + '</h2>' + bodyHTML + '</section>';
    };

    const renderMetric = function(label, value, hint) {
        return '<div class="metric"><span>' + escapeHTML(label) + '</span><strong>' + escapeHTML(value) + '</strong>' + (hint ? '<em>' + escapeHTML(hint) + '</em>' : '') + '</div>';
    };

    const formatScore = function(value, suffix) {
        if (value === null || value === undefined || value === '') return 'n/a';
        const num = Number(value);
        if (!Number.isFinite(num)) return escapeHTML(value);
        return Math.round(num).toString() + (suffix || '');
    };

    const getDossier = function(exportData) {
        const domain = exportData?.domain;
        if (!domain || !observatoryData?.domain_dossiers) return null;
        return observatoryData.domain_dossiers.find(function(d) { return d.domain === domain; }) || null;
    };

    const buildAndDownloadExecutive = function(exportData) {
        const companyName = exportData.companyName || exportData.company_name || exportData.name || 'Company';
        const generatedDate = new Date().toISOString().split('T')[0];
        const gap = Number(exportData.intent_score || 0) - Number(exportData.reality_score || 0);
        const evidencePool = []
            .concat(Array.isArray(exportData.evidence_links) ? exportData.evidence_links : [])
            .concat(Array.isArray(exportData.foresight_links) ? exportData.foresight_links : []);
        const isVerificationConflict = function(link) {
            return link && (link.verification_conflict === true || link.quote_mismatch === true || link.claim_irrelevant === true);
        };
        const isEntitySafe = function(link) {
            const status = String(link && (link.content_entity_match_status || link.entity_match_status) || '').toLowerCase();
            return link && (link.entity_verified === true || status === 'exact' || status === 'scoped');
        };
        const isClaimSupported = function(link) {
            return link && String(link.claim_support_status || '').toLowerCase() === 'supported' && !isVerificationConflict(link);
        };
        const isAnalysisSafe = function(link) {
            return link && link.analysis_verified === true && link.promotion_status === 'generation_eligible' && link.generation_eligible === true && isEntitySafe(link) && isClaimSupported(link);
        };
        const isWrongEntity = function(link) {
            return link && (link.wrong_entity === true || String(link.entity_match_status || '').toLowerCase() === 'wrong');
        };
        const isUnknownEntity = function(link) {
            const status = String(link?.entity_match_status || '').toLowerCase();
            const scope = String(link?.entity_scope || '').toLowerCase();
            return link && !isAnalysisSafe(link) && !isWrongEntity(link) && (status === 'weak' || status === 'unknown' || (!!(link.url || link.source_url) && !status) || scope === 'unknown');
        };
        const verifiedSources = evidencePool.filter(function(link) {
            return link && (link.verified === true || link.url_verified === true);
        }).length;
        const analysisVerifiedSources = evidencePool.filter(function(link) {
            return isAnalysisSafe(link);
        }).length;
        const generationEligibleSources = evidencePool.filter(function(link) {
            return isAnalysisSafe(link) && link.generation_eligible === true;
        }).length;
        const displayOnlySources = evidencePool.filter(function(link) {
            return link && (link.promotion_status === 'display_only' || link.claim_support_status === 'unsupported');
        }).length;
        const wrongEntitySources = evidencePool.filter(isWrongEntity).length;
        const unknownEntitySources = evidencePool.filter(isUnknownEntity).length;
        const weakSources = evidencePool.filter(function(link) {
            const cred = String(link?.source_credibility || link?.credibility || '').toLowerCase();
            const support = String(link?.claim_support_status || '').toLowerCase();
            const promotion = String(link?.promotion_status || '').toLowerCase();
            return link && (
                link.verified === false
                || link.url_verified === false
                || cred === 'low'
                || (!cred && (link.url || link.link))
                || ['unsupported', 'unchecked', 'unavailable', 'partial'].includes(support)
                || ['display_only', 'excluded'].includes(promotion)
                || isWrongEntity(link)
                || isUnknownEntity(link)
            );
        }).length;
        const carriedForward = evidencePool.some(function(link) {
            return String(link?.source_type || '').toLowerCase() === 'historical';
        }) || (exportData.reality_checklist || []).some(function(item) {
            return String(item?.source_type || '').toLowerCase() === 'historical';
        });
        const isSummaryLengthWarning = function(note) {
            return /Summary (?:unusually long|length outside target range) \(\d+ words, expected 325-(?:375|475)\)/i.test(String(note || ''));
        };
        const unsupportedClaimCount = (Array.isArray(exportData.hallucination_flags) ? exportData.hallucination_flags.filter(function(flag) { return !isSummaryLengthWarning(flag); }).length : 0)
            + (Array.isArray(exportData.ai_verification?.false_claims) ? exportData.ai_verification.false_claims.length : 0)
            + (Array.isArray(exportData.ai_verification?.unsupported_claims) ? exportData.ai_verification.unsupported_claims.length : 0)
            + (Array.isArray(exportData.general_summary_false_claims) ? exportData.general_summary_false_claims.length : 0);
        const evidenceSufficiency = exportData.evidence_sufficiency && typeof exportData.evidence_sufficiency === 'object' ? exportData.evidence_sufficiency : null;
        const lowEvidenceLabel = exportData.low_evidence_label || (evidenceSufficiency && evidenceSufficiency.label) || '';
        const isLowEvidenceMode = exportData.low_evidence_mode === true || !!lowEvidenceLabel;

        const metrics = [
            renderMetric('Ambition Score', formatScore(exportData.intent_score, '/100'), 'Narrative clarity'),
            renderMetric('Reality Score', formatScore(exportData.reality_score, '/100'), 'Evidence check'),
            renderMetric('Integrity Delta', (gap > 0 ? '+' : '') + Math.round(gap), 'Ambition minus reality'),
            renderMetric('Confidence', formatScore(exportData.confidence_score ?? exportData.scan_quality?.confidence_score ?? exportData.evidence_sufficiency?.score, '%'), 'Model confidence'),
            renderMetric('Financial Health', formatScore(exportData.financial_index, '/100'), 'Adaptation capacity'),
            renderMetric('URL Verified Sources', verifiedSources, weakSources + ' weak/unverified'),
            renderMetric('Analysis Verified', analysisVerifiedSources, 'content supports claim'),
            renderMetric('Generation Eligible', generationEligibleSources, 'safe for prose'),
            renderMetric('Source Register', isLowEvidenceMode ? (lowEvidenceLabel || 'Low evidence') : 'Complete', isLowEvidenceMode ? 'cautious prose mode' : 'promoted evidence available'),
            renderMetric('Display / Unsupported', displayOnlySources, 'audit only'),
            renderMetric('Wrong Entity', wrongEntitySources, 'blocked sources'),
            renderMetric('Unknown Entity', unknownEntitySources, 'not promoted'),
            renderMetric('Evidence Links', evidencePool.length, 'Company and foresight sources'),
            renderMetric('Unsupported Flags', unsupportedClaimCount, 'Hallucination and false-claim checks'),
            renderMetric('Washing Flag', exportData.washing_flag ? 'Yes' : 'No', exportData.washing_type || 'Narrative vs evidence'),
            renderMetric('Carried Forward', carriedForward ? 'Yes' : 'No', 'Previous-scan evidence')
        ];
        if (faviData && faviData.segment) {
            const faviCutoff = faviData.favi_cutoff ?? faviData.favi_cutoffs?.favi ?? 50;
            const facCutoff = faviData.fac_cutoff ?? 50;
            const adaptationSegment = getAdaptationSegment(faviData, faviCutoff, facCutoff);
            metrics.push(renderMetric('Adaptation Segment', adaptationSegment, 'FAC x FAVI'));
            metrics.push(renderMetric('FAVI', formatScore(faviData.favi_score, '/100'), 'Foresight vulnerability'));
            if (faviData.fps != null) {
                metrics.push(renderMetric('Foresight Pressure', formatScore(faviData.fps, '/100'), 'FPS'));
            }
            if (faviData.readiness_gap != null) {
                metrics.push(renderMetric('Readiness Gap', formatScore(faviData.readiness_gap, '/100'), faviData.readiness_gap_source || 'Current x future gap'));
            }
            if (faviData.fac != null) {
                metrics.push(renderMetric('Adaptation Capacity', formatScore(faviData.fac, '/100'), 'FAC'));
            }
            if (faviData.current_vulnerability != null || faviData.current_vulnerability_signal != null) {
                metrics.push(renderMetric('Current Vulnerability', formatScore(faviData.current_vulnerability ?? faviData.current_vulnerability_signal, '/100'), 'Portfolio-relative'));
            }
        }

        const headerMeta = [
            exportData.domain ? '<span>' + escapeHTML(exportData.domain) + '</span>' : '',
            exportData.archetype ? '<span>' + escapeHTML(exportData.archetype) + '</span>' : '',
            exportData.lastUpdated ? '<span>Scanned ' + escapeHTML(new Date(exportData.lastUpdated).toLocaleDateString('en-FI', { day: 'numeric', month: 'long', year: 'numeric' })) + '</span>' : ''
        ].filter(Boolean).join('');

        const whyWhatHTML = '<div class="two-col"><div><h3>WHY</h3>' + renderList(exportData.why_proposals) + '</div><div><h3>WHAT</h3>' + renderList(exportData.what_proposals) + '</div></div>';
        const pitchHTML = renderParagraphs(exportData.pitch_text);
        const diagnosisHTML = renderParagraphs(exportData.summary_text)
            + (Array.isArray(exportData.evidence_based_risks) && exportData.evidence_based_risks.length > 0
                ? '<div class="callout"><h3>Evidence-Based Risks</h3>' + renderList(exportData.evidence_based_risks) + '</div>'
                : '');

        const dossier = getDossier(exportData);
        let dossierHTML = '';
        if (dossier?.text) {
            const archDist = dossier.archetype_distribution || {};
            const forces = dossier.forces_by_timescale || {};
            const dossierStats = '<div class="chips">'
                + (dossier.company_count != null ? '<span>' + escapeHTML(dossier.company_count) + ' companies</span>' : '')
                + Object.keys(archDist).map(function(key) { return '<span>' + escapeHTML(key) + ': ' + escapeHTML(archDist[key]) + '</span>'; }).join('')
                + (forces.act_now?.length ? '<span>Act Now: ' + escapeHTML(forces.act_now.length) + '</span>' : '')
                + (forces.prepare?.length ? '<span>Prepare: ' + escapeHTML(forces.prepare.length) + '</span>' : '')
                + (forces.position?.length ? '<span>Position: ' + escapeHTML(forces.position.length) + '</span>' : '')
                + '</div>';
            const dossierBody = String(dossier.text).split(/\n\n+/).filter(function(block) {
                const trimmed = block.trim();
                return trimmed && !/^#\s+Domain Force Dossier/i.test(trimmed);
            }).join('\n\n');
            dossierHTML = '<div class="callout cyan"><h3>Scanner Portfolio Context</h3><p>These domain metrics are derived from the Scanner’s analyzed company portfolio. They summarize observed company statuses and foresight pressure inside the Scanner dataset, rather than claiming to be a complete external market census.</p></div>'
                + dossierStats + renderReportBlocks(dossierBody);
        }

        const htmlContent = '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
            + '<meta charset="UTF-8">\n'
            + '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
            + '<title>' + escapeHTML(companyName) + ' - Executive Summary</title>\n'
            + '<style>\n'
            + ':root{color-scheme:dark;--bg:#0f172a;--panel:#172033;--panel2:#111827;--line:#334155;--text:#e5e7eb;--muted:#94a3b8;--cyan:#67e8f9;--green:#6ee7b7;--amber:#fbbf24;--rose:#fda4af;--violet:#c4b5fd;}\n'
            + '*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,Arial,sans-serif;line-height:1.55}main{max-width:1120px;margin:0 auto;padding:40px 28px 56px}.hero{border:1px solid var(--line);background:linear-gradient(135deg,#172033,#0f172a);border-radius:8px;padding:30px;margin-bottom:20px}.kicker{color:var(--cyan);font-size:11px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}.hero h1{margin:8px 0 10px;font-size:34px;line-height:1.1}.meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}.meta span,.chips span{border:1px solid rgba(148,163,184,.28);background:rgba(15,23,42,.62);border-radius:999px;padding:5px 9px;color:var(--muted);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:20px 0}.metric{border:1px solid rgba(148,163,184,.22);background:rgba(15,23,42,.72);border-radius:8px;padding:14px}.metric span{display:block;color:var(--muted);font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.1em}.metric strong{display:block;margin-top:4px;font-size:24px;color:white}.metric em{display:block;margin-top:3px;color:var(--muted);font-size:11px;font-style:normal}.section{border:1px solid rgba(148,163,184,.22);background:rgba(17,24,39,.78);border-radius:8px;padding:24px;margin-top:16px}.section h2{margin:0 0 14px;color:white;font-size:20px}.section h3{margin:20px 0 8px;color:var(--cyan);font-size:14px;text-transform:uppercase;letter-spacing:.08em}.section h4{margin:16px 0 6px;color:white;font-size:14px}.section p{margin:0 0 13px;color:#dbe4f0}.eyebrow{margin-bottom:6px;color:var(--muted);font-size:10px;font-weight:800;letter-spacing:.14em;text-transform:uppercase}.two-col{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}.two-col h3{color:var(--amber)}ul{margin:0;padding-left:20px}li{margin:8px 0}.callout{margin-top:18px;border-left:3px solid var(--rose);background:rgba(244,63,94,.08);padding:14px 16px}.callout.cyan{border-left-color:var(--cyan);background:rgba(34,211,238,.08)}.stakeholders{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}.stakeholders article{border:1px solid rgba(148,163,184,.18);background:rgba(15,23,42,.55);border-radius:8px;padding:15px}.stakeholders h3{margin:0;color:white;text-transform:none;letter-spacing:0;font-size:15px}.role{color:var(--green);font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;margin:4px 0 8px}.stakeholders small{display:block;margin-top:10px;color:var(--muted);font-size:11px}.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}.table-wrap{overflow-x:auto;margin-top:10px;margin-bottom:18px;border:1px solid rgba(148,163,184,.18);border-radius:8px}table{width:100%;border-collapse:collapse;font-size:11px;min-width:820px}th,td{padding:8px 10px;text-align:left;border-bottom:1px solid rgba(148,163,184,.14)}th{color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.08em;background:rgba(15,23,42,.68)}td{color:#dbe4f0}td:nth-child(4){color:var(--rose);font-weight:800}.scanner-link{display:inline-block;border:1px solid rgba(103,232,249,.38);background:rgba(8,145,178,.18);color:var(--cyan);border-radius:8px;padding:10px 14px;font-weight:800;text-decoration:none}.scanner-link:hover{background:rgba(8,145,178,.28);color:white}.formula{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px}.muted{color:var(--muted)!important}strong{color:white}hr{border:0;border-top:1px solid var(--line);margin:20px 0}.footer{margin-top:28px;color:#64748b;font-size:11px;text-align:center}.page-note{color:var(--muted);max-width:820px;margin:0}\n'
            + '</style>\n</head>\n<body>\n<main>\n'
            + '<header class="hero"><div class="kicker">Executive Summary HTML Export</div><h1>' + escapeHTML(companyName) + '</h1><p class="page-note">Compact context package for leadership review and upload workflows. This file intentionally excludes the full raw JSON payload.</p><div class="meta">' + headerMeta + '<span>Generated ' + escapeHTML(generatedDate) + '</span></div></header>\n'
            + (isLowEvidenceMode ? '<section class="section"><div class="eyebrow">Evidence status</div><h2>' + escapeHTML(lowEvidenceLabel || 'Low promoted evidence') + '</h2><p class="muted">Promoted source-register evidence is limited. The summary remains usable, but precise external company claims require source-backed support.</p></section>' : '')
            + renderSection('General Summary', 'Executive context', renderParagraphs(exportData.general_summary))
            + renderSection('AI Diagnosis', 'Interpretation and risks', diagnosisHTML)
            + renderSection('Core Metrics', 'Scorecard', '<div class="metrics">' + metrics.join('') + '</div>')
            + renderSection('WHY and WHAT', 'Activation thesis', whyWhatHTML)
            + renderSection('Strategic Pitch', 'Outbound narrative', pitchHTML)
            + renderSection(exportData.domain ? 'Domain Force Dossier: ' + exportData.domain : 'Domain Force Dossier', 'Market context', dossierHTML)
            + renderSection('Detailed Forensic Report', 'Diagnostic detail', renderReportBlocks(exportData.detailed_report))
            + '<section class="section"><div class="eyebrow">Continue analysis</div><h2>See Detailed Report</h2><p class="muted">For the full interactive scanner view, source context, and deeper company diagnostics, open the Scanner.</p><p><a class="scanner-link" href="https://the-demand-scanner.up.railway.app/" target="_blank" rel="noopener noreferrer">See detailed report - Scanner</a></p></section>'
            + '<div class="footer">AdaptabilityScanner | Executive export | No raw JSON embedded</div>\n'
            + '</main>\n</body>\n</html>';

        const blob = new Blob([htmlContent], { type: 'text/html' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'Executive_Report_' + String(companyName).replace(/\s+/g, '_') + '_' + generatedDate + '.html';
        a.click();
        URL.revokeObjectURL(url);
    };

    fetch('/company/' + encodeURIComponent(data.companyName) + '?t=' + Date.now())
        .then(function(res) {
            if (res.ok) return res.json();
            console.warn('[Executive HTML Export] Fetch response not ok:', res.status);
            return null;
        })
        .catch(function(err) {
            console.warn('[Executive HTML Export] Fetch error:', err);
            return null;
        })
        .then(function(fresh) {
            const exportData = fresh ? Object.assign({}, data, fresh) : data;
            buildAndDownloadExecutive(exportData);
        });
  };

  const handleDownloadBuyingInsight = () => {
    if (!buyingInsightMeta?.html_url) return;
    const generatedDate = new Date().toISOString().split('T')[0];
    const safeName = String(companyDisplayName || 'Company').replace(/\s+/g, '_');
    const a = document.createElement('a');
    a.href = buyingInsightMeta.html_url;
    a.download = safeName + '_Buying_Insight_Scanner_' + generatedDate + '.html';
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.click();
  };

  return (
    <div className="space-y-4">
      {/* TOOLBAR */}
      <div className="flex justify-end items-center gap-2 mb-2">
        <button 
            onClick={handleCopyJSON}
            className="flex items-center gap-2 px-3 py-1.5 rounded bg-white/5 hover:bg-white/10 border border-white/5 hover:border-cyan-500/30 text-xs font-mono text-cyan-400 transition-all"
            title="Copy Raw JSON to Clipboard for Deep Dive Tool"
        >
            {copied ? <Check size={14} /> : <FileCode size={14} />}
            {copied ? "COPIED" : "COPY JSON"}
        </button>
        <button
            onClick={handleDownloadExecutiveHTML}
            className="flex items-center gap-2 px-3 py-1.5 rounded bg-cyan-600/80 hover:bg-cyan-500 border border-cyan-300/30 text-xs font-bold text-white transition-all shadow-lg shadow-cyan-900/20"
            title="Download Executive Summary HTML (No Raw JSON)"
        >
            <Download size={14} />
            EXEC SUMMARY
        </button>
        {isAdmin && buyingInsightMeta?.html_url && (
        <button
            onClick={handleDownloadBuyingInsight}
            className="flex items-center gap-2 px-3 py-1.5 rounded bg-emerald-600/80 hover:bg-emerald-500 border border-emerald-300/30 text-xs font-bold text-white transition-all shadow-lg shadow-emerald-900/20"
            title="Download stored Buying Potential & Insight HTML"
        >
            <Download size={14} />
            BUYING INSIGHT
        </button>
        )}
        <button 
            onClick={handleDownloadHTML}
            className="flex items-center gap-2 px-3 py-1.5 rounded bg-indigo-600/80 hover:bg-indigo-500 border border-indigo-400/30 text-xs font-bold text-white transition-all shadow-lg shadow-indigo-900/20"
            title="Download Master Data HTML (Complete evidence, analysis, and raw JSON)"
        >
            <Download size={14} />
            MASTER DATA
        </button>
      </div>

      {/* CONTENT WRAPPER FOR SNAPSHOT */}
      <div ref={summaryRef} className="space-y-8">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-white/5 backdrop-blur-md p-6 rounded-2xl border border-white/10 flex flex-col items-center justify-center relative group overflow-hidden">
            <div className="absolute inset-0 bg-indigo-500/10 opacity-0 group-hover:opacity-100 transition-opacity"></div>
            <span className="text-indigo-200 font-medium mb-1 text-sm tracking-widest uppercase">Ambition Score</span>
            <span className="text-6xl font-black text-white tracking-tighter drop-shadow-[0_0_15px_rgba(99,102,241,0.5)]">{data.intent_score}</span>
            <span className="text-xs text-slate-400 mt-2 font-mono">Narrative Clarity</span>
            </div>
            
            <div className="bg-white/5 backdrop-blur-md p-6 rounded-2xl border border-white/10 flex flex-col items-center justify-center relative group overflow-hidden">
            <div className="absolute inset-0 bg-emerald-500/10 opacity-0 group-hover:opacity-100 transition-opacity"></div>
            <span className="text-emerald-200 font-medium mb-1 text-sm tracking-widest uppercase">Reality Score</span>
            <span className="text-6xl font-black text-white tracking-tighter drop-shadow-[0_0_15px_rgba(16,185,129,0.5)]">{data.reality_score}</span>
            <span className="text-xs text-slate-400 mt-2 font-mono">Evidence Check</span>
            </div>
            
            <div className="bg-white/5 backdrop-blur-md p-6 rounded-2xl border border-white/10 flex flex-col items-center justify-center relative overflow-hidden group/tooltip">
                <div className="absolute inset-0 bg-gradient-to-br from-transparent to-white/5 opacity-0 group-hover/tooltip:opacity-100 transition-opacity"></div>
                <span className="text-slate-300 font-medium mb-1 text-sm tracking-widest uppercase">Integrity Delta</span>
                <span className="text-6xl font-black tracking-tighter" style={{ color: gapColor, textShadow: `0 0 20px ${gapColor}40` }}>
                    {gapValueDisplay}
                </span>
                
                {/* TOOLTIP CONTAINER */}
                <div className="flex items-center gap-1.5 mt-2 z-10 relative cursor-help">
                    <span className="text-xs font-mono" style={{ color: gapColor }}>{gapLabel}</span>
                    <Info size={12} className="text-slate-500 hover:text-white transition-colors" />
                    
                    {/* HOVER CONTENT */}
                    <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-48 p-3 bg-slate-900 border border-white/20 rounded-lg shadow-xl text-[10px] text-slate-300 pointer-events-none opacity-0 group-hover/tooltip:opacity-100 transition-opacity z-50 text-center leading-relaxed backdrop-blur-xl">
                        {ARCHETYPE_DESCRIPTIONS[gapKey] || "No definition available."}
                        <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-2 h-2 bg-slate-900 border-b border-r border-white/20 rotate-45"></div>
                    </div>
                </div>
            </div>
        </div>

        {/* WASHING WARNING BANNER */}
        {/* LOGIC FIX: Ensure Washing only displays if Intent > Reality. */}
        {data.washing_flag && data.intent_score > data.reality_score && (
            <div className="animate-in slide-in-from-top-4 bg-rose-500/10 border-l-4 border-rose-500 p-4 rounded-r-xl flex items-start gap-4 shadow-lg shadow-rose-900/20">
                <AlertOctagon className="text-rose-500 shrink-0 mt-0.5" size={24} />
                <div>
                    <h4 className="text-rose-400 font-bold text-sm uppercase tracking-wide">
                        {data.washing_type ? data.washing_type.toUpperCase() : "MODERNIZATION WASHING DETECTED"}
                    </h4>
                    <p className="text-slate-300 text-sm mt-1">
                        Forensic analysis confirms a significant contradiction between marketing intent and operational reality. 
                        High-level claims ({data.intent_score} Ambition) are unsupported by technical evidence.
                    </p>
                </div>
            </div>
        )}

        {/* METRICS & TRENDS ROW */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="space-y-4">
                <div className="bg-white/5 p-4 rounded-xl border border-white/10 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <div className="p-2 bg-slate-800 rounded-lg text-slate-400">
                            <Scale size={20} />
                        </div>
                        <div>
                            <h4 className="text-xs text-slate-400 font-bold uppercase tracking-wider">Friction Index</h4>
                            <p className={`text-lg font-bold ${frictionColor}`}>{frictionLabel} ({frictionPercentage}%)</p>
                        </div>
                    </div>
                    <div className="text-right">
                        <span className="text-[10px] text-slate-500 block">Good Signals: {metrics.total_good_mentions}</span>
                        <span className="text-[10px] text-slate-500 block">Bad Signals: {metrics.total_bad_mentions}</span>
                    </div>
                </div>

                {faviData && faviData.segment && (() => {
                    const faviCutoff = faviData.favi_cutoff ?? faviData.favi_cutoffs?.favi ?? 50;
                    const facCutoff = faviData.fac_cutoff ?? 50;
                    const adaptationSegment = getAdaptationSegment(faviData, faviCutoff, facCutoff);
                    const seg = ADAPTATION_SEGMENT_CONFIG[adaptationSegment] || ADAPTATION_SEGMENT_CONFIG["Mature & Stable"];
                    return (
                        <div className={`${seg.bg} p-4 rounded-xl border ${seg.border} flex items-center justify-between`}>
                            <div className="flex items-center gap-3">
                                <div className={`p-2 bg-slate-800 rounded-lg ${seg.icon}`}>
                                    <Eye size={20} />
                                </div>
                                <div>
                                    <h4 className="text-xs text-slate-400 font-bold uppercase tracking-wider">Adaptation x Future Vulnerability</h4>
                                    <p className={`text-lg font-bold ${seg.color}`}>{adaptationSegment}</p>
                                </div>
                            </div>
                            <div className="text-right">
                                <span className="text-[10px] text-slate-500 block">{seg.description}</span>
                                <div className="flex items-center justify-end gap-2 mt-1">
                                    <span className="text-[10px] text-slate-500">Foresight Vulnerability</span>
                                    <span className={`text-sm font-mono font-bold ${seg.color}`}>{faviData.favi_score}</span>
                                </div>
                                {(faviData.fps != null || faviData.readiness_gap != null || faviData.current_vulnerability_signal != null) && (
                                    <div className="flex flex-wrap items-center justify-end gap-2 mt-2 text-[10px] text-slate-400">
                                        {faviData.fps != null && <span>FPS {faviData.fps}</span>}
                                        {faviData.readiness_gap != null && <span>Gap {faviData.readiness_gap}</span>}
                                        {faviData.current_vulnerability_signal != null && <span>Current {faviData.current_vulnerability_signal}</span>}
                                        {faviData.readiness_gap_source === 'current_vulnerability_adjusted' && (
                                            <span className="text-amber-300">current-risk adjusted</span>
                                        )}
                                    </div>
                                )}
                            </div>
                        </div>
                    );
                })()}

            </div>

            {/* TREND CHART */}
            <div className="bg-white/5 p-5 rounded-2xl border border-white/10 backdrop-blur-md flex flex-col">
                <h3 className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-4 flex items-center gap-2">
                <Activity className="text-cyan-400" size={14} />
                Integrity Delta Trend (6 Mo)
                </h3>
                <div className="flex-grow min-h-[120px]">
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={chartData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.2} vertical={false} />
                            <XAxis 
                                dataKey="date" 
                                stroke="#64748b" 
                                fontSize={10} 
                                tickLine={false} 
                                axisLine={false}
                                dy={5} 
                            />
                            <YAxis 
                                stroke="#64748b" 
                                fontSize={10} 
                                tickLine={false} 
                                axisLine={false}
                                domain={[-100, 100]} 
                                hide={true} 
                            />
                            <ReferenceLine y={0} stroke="#475569" strokeDasharray="3 3" />
                            <RechartsTooltip 
                                contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', color: '#f1f5f9', borderRadius: '8px', fontSize: '12px', padding: '8px' }}
                                itemStyle={{ color: gapColor }}
                            />
                            <Line 
                                type="monotone" 
                                dataKey="gap" 
                                stroke={gapColor} 
                                strokeWidth={3} 
                                dot={{ fill: '#0f172a', stroke: gapColor, strokeWidth: 2, r: 4 }}
                                activeDot={{ r: 6, fill: gapColor, stroke: '#fff' }}
                                animationDuration={1500}
                            />
                        </LineChart>
                    </ResponsiveContainer>
                </div>
            </div>
        </div>

        {/* FINANCIAL HEALTH INDEX */}
        {data.financial_index != null && (
            <div className="bg-white/5 p-6 rounded-2xl border border-white/10 backdrop-blur-md">
                <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                    <TrendingUp className="text-cyan-400" size={20} />
                    Financial Health Index
                </h3>
                <div className="flex flex-col md:flex-row items-center gap-6">
                    {/* Big score */}
                    <div className="flex flex-col items-center justify-center min-w-[120px]">
                        <span className={`text-5xl font-black tracking-tighter ${
                            data.financial_index >= 70 ? 'text-emerald-400 drop-shadow-[0_0_15px_rgba(16,185,129,0.5)]' :
                            data.financial_index >= 45 ? 'text-amber-400 drop-shadow-[0_0_15px_rgba(245,158,11,0.5)]' :
                            'text-rose-400 drop-shadow-[0_0_15px_rgba(244,63,94,0.5)]'
                        }`}>{data.financial_index}</span>
                        <span className="text-xs text-slate-400 mt-1 font-mono">/ 100</span>
                    </div>

                    {/* Sub-score bars */}
                    <div className="flex-grow w-full grid grid-cols-1 sm:grid-cols-2 gap-3">
                        {[
                            { key: 'revenue_trend', label: 'Revenue Trend', icon: '📈' },
                            { key: 'profitability', label: 'Profitability', icon: '💰' },
                            { key: 'financial_stability', label: 'Financial Stability', icon: '🏦' },
                            { key: 'investment_signal', label: 'Investment Signal', icon: '🔬' }
                        ].map(dim => {
                            const score = data.financial_sub_scores?.[dim.key] ?? 0;
                            const pct = Math.round((score / 25) * 100);
                            const barColor = score >= 18 ? 'bg-emerald-500' : score >= 12 ? 'bg-amber-500' : 'bg-rose-500';
                            return (
                                <div key={dim.key} className="bg-black/20 p-3 rounded-lg border border-white/5">
                                    <div className="flex items-center justify-between mb-1.5">
                                        <span className="text-[10px] text-slate-400 uppercase tracking-wider font-bold">{dim.icon} {dim.label}</span>
                                        <span className="text-xs font-mono font-bold text-slate-300">{score}/25</span>
                                    </div>
                                    <div className="w-full h-1.5 bg-slate-700 rounded-full">
                                        <div className={`h-full ${barColor} rounded-full transition-all duration-500`} style={{ width: `${pct}%` }}></div>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
                {data.financial_confidence != null && (
                    <div className="mt-3 text-[10px] text-slate-500 font-mono flex items-center gap-1.5">
                        <Eye size={10} /> Data Confidence: {data.financial_confidence}%
                    </div>
                )}
            </div>
        )}

        {/* EVIDENCE TRUST SECTION */}
        <div className="bg-white/5 p-6 rounded-2xl border border-white/10 backdrop-blur-md">
            <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4 mb-5">
                <div>
                    <h3 className="text-lg font-bold text-white flex items-center gap-2">
                        <ShieldCheck className="text-cyan-400" size={20} />
                        Evidence Trust
                    </h3>
                    <p className="text-[10px] text-slate-500 uppercase tracking-wider font-mono mt-1">
                        Source quality, verification status, and scan carry-forward signals
                    </p>
                </div>
                <div className="flex items-center gap-2 text-[10px] font-mono">
                    {isLowEvidenceMode && (
                        <span className="px-2 py-1 rounded border bg-amber-500/10 text-amber-300 border-amber-500/30">
                            {lowEvidenceLabel || 'Low promoted evidence'}
                        </span>
                    )}
                    <span className={`px-2 py-1 rounded border ${previousScanCarriedForward ? 'bg-amber-500/10 text-amber-300 border-amber-500/30' : 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30'}`}>
                        Carried forward: {previousScanCarriedForward ? 'Yes' : 'No'}
                    </span>
                    <span className="px-2 py-1 rounded border bg-slate-800/70 text-slate-400 border-white/10">
                        {evidenceTrustPool.length} total sources
                    </span>
                </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
                <div className="bg-black/20 p-4 rounded-xl border border-emerald-500/20">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] text-slate-500 uppercase tracking-wider font-bold">URL Verified</span>
                        <CheckCircle size={14} className="text-emerald-400" />
                    </div>
                    <p className="text-3xl font-black text-emerald-300">{verifiedSourceCount}</p>
                    <p className="text-[10px] text-slate-500 mt-1">reachable sources</p>
                </div>
                <div className="bg-black/20 p-4 rounded-xl border border-teal-500/20">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] text-slate-500 uppercase tracking-wider font-bold">Content Readable</span>
                        <ShieldCheck size={14} className="text-teal-300" />
                    </div>
                    <p className="text-3xl font-black text-teal-300">{contentReadableCount}</p>
                    <p className="text-[10px] text-slate-500 mt-1">readable page text</p>
                </div>
                <div className="bg-black/20 p-4 rounded-xl border border-cyan-500/20">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] text-slate-500 uppercase tracking-wider font-bold">Target Entity Verified</span>
                        <CheckCircle size={14} className="text-cyan-300" />
                    </div>
                    <p className="text-3xl font-black text-cyan-300">{entityVerifiedCount}</p>
                    <p className="text-[10px] text-slate-500 mt-1">right company or scope</p>
                </div>
                <div className="bg-black/20 p-4 rounded-xl border border-sky-500/20">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] text-slate-500 uppercase tracking-wider font-bold">Claim Supported</span>
                        <CheckCircle size={14} className="text-sky-300" />
                    </div>
                    <p className="text-3xl font-black text-sky-300">{claimSupportedCount}</p>
                    <p className="text-[10px] text-slate-500 mt-1">quote supports claim</p>
                </div>
                <div className="bg-black/20 p-4 rounded-xl border border-cyan-500/20">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] text-slate-500 uppercase tracking-wider font-bold">Generation Eligible</span>
                        <CheckCircle size={14} className="text-cyan-300" />
                    </div>
                    <p className="text-3xl font-black text-cyan-300">{generationEligibleCount}</p>
                    <p className="text-[10px] text-slate-500 mt-1">safe for prose</p>
                </div>
                <div className="bg-black/20 p-4 rounded-xl border border-amber-500/20">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] text-slate-500 uppercase tracking-wider font-bold">Weak / Unverified</span>
                        <AlertTriangle size={14} className="text-amber-400" />
                    </div>
                    <p className="text-3xl font-black text-amber-300">{weakOrUnverifiedSourceCount}</p>
                    <p className="text-[10px] text-slate-500 mt-1">needs caution</p>
                </div>
                <div className="bg-black/20 p-4 rounded-xl border border-slate-500/20">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] text-slate-500 uppercase tracking-wider font-bold">Display / Unsupported</span>
                        <Info size={14} className="text-slate-400" />
                    </div>
                    <p className="text-3xl font-black text-slate-300">{displayOnlyCount}</p>
                    <p className="text-[10px] text-slate-500 mt-1">audit only</p>
                </div>
                <div className="bg-black/20 p-4 rounded-xl border border-rose-500/20">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] text-slate-500 uppercase tracking-wider font-bold">Wrong Entity</span>
                        <AlertOctagon size={14} className="text-rose-400" />
                    </div>
                    <p className="text-3xl font-black text-rose-300">{wrongEntityCount}</p>
                    <p className="text-[10px] text-slate-500 mt-1">blocked sources</p>
                </div>
                <div className="bg-black/20 p-4 rounded-xl border border-fuchsia-500/20">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] text-slate-500 uppercase tracking-wider font-bold">Verification Conflict</span>
                        <AlertOctagon size={14} className="text-fuchsia-300" />
                    </div>
                    <p className="text-3xl font-black text-fuchsia-300">{verificationConflictCount}</p>
                    <p className="text-[10px] text-slate-500 mt-1">blocked conflicts</p>
                </div>
                <div className="bg-black/20 p-4 rounded-xl border border-orange-500/20">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] text-slate-500 uppercase tracking-wider font-bold">Unknown Entity</span>
                        <AlertTriangle size={14} className="text-orange-300" />
                    </div>
                    <p className="text-3xl font-black text-orange-300">{unknownEntityCount}</p>
                    <p className="text-[10px] text-slate-500 mt-1">not promoted</p>
                </div>
                <div className="bg-black/20 p-4 rounded-xl border border-cyan-500/20">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] text-slate-500 uppercase tracking-wider font-bold">Confidence</span>
                        <Activity size={14} className="text-cyan-400" />
                    </div>
                    <p className={`text-3xl font-black ${confidenceClass}`}>
                        {confidenceScore == null ? 'N/A' : confidenceScore}
                    </p>
                    <p className="text-[10px] text-slate-500 mt-1">{confidenceScore == null ? 'not reported' : '/ 100'}</p>
                </div>
                <div className="bg-black/20 p-4 rounded-xl border border-indigo-500/20">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] text-slate-500 uppercase tracking-wider font-bold">Credibility</span>
                        <Eye size={14} className="text-indigo-400" />
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 font-mono">{credibilityCounts.high} high</span>
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 font-mono">{credibilityCounts.medium} medium</span>
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-rose-500/15 text-rose-300 font-mono">{credibilityCounts.low} low</span>
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-600/30 text-slate-400 font-mono">{credibilityCounts.unknown} unknown</span>
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-4 gap-3 mt-4">
                <div className={`p-4 rounded-xl border ${contradictionWarnings.length > 0 ? 'bg-amber-500/10 border-amber-500/30' : 'bg-black/20 border-white/5'}`}>
                    <div className="flex items-center gap-2 mb-2">
                        <Scale size={14} className={contradictionWarnings.length > 0 ? 'text-amber-400' : 'text-slate-500'} />
                        <span className="text-[10px] text-slate-400 uppercase tracking-wider font-bold">Contradiction Warnings</span>
                    </div>
                    {contradictionWarnings.length > 0 ? (
                        <div className="space-y-1.5">
                            {contradictionWarnings.slice(0, 4).map((warning, i) => (
                                <p key={i} className="text-[11px] text-amber-100/90 leading-relaxed font-mono">- {warning}</p>
                            ))}
                        </div>
                    ) : (
                        <p className="text-xs text-slate-500">None detected.</p>
                    )}
                </div>
                <div className={`p-4 rounded-xl border ${wrongEntityWarnings.length > 0 ? 'bg-rose-500/10 border-rose-500/30' : 'bg-black/20 border-white/5'}`}>
                    <div className="flex items-center gap-2 mb-2">
                        <AlertOctagon size={14} className={wrongEntityWarnings.length > 0 ? 'text-rose-400' : 'text-slate-500'} />
                        <span className="text-[10px] text-slate-400 uppercase tracking-wider font-bold">Wrong-Entity Warnings</span>
                    </div>
                    {wrongEntityWarnings.length > 0 ? (
                        <div className="space-y-1.5">
                            {wrongEntityWarnings.slice(0, 5).map((warning, i) => (
                                <p key={i} className="text-[11px] text-rose-100/90 leading-relaxed font-mono">- {warning}</p>
                            ))}
                        </div>
                    ) : (
                        <p className="text-xs text-slate-500">None detected.</p>
                    )}
                </div>
                <div className={`p-4 rounded-xl border ${hallucinationFlags.length > 0 ? 'bg-rose-500/10 border-rose-500/30' : 'bg-black/20 border-white/5'}`}>
                    <div className="flex items-center gap-2 mb-2">
                        <AlertOctagon size={14} className={hallucinationFlags.length > 0 ? 'text-rose-400' : 'text-slate-500'} />
                        <span className="text-[10px] text-slate-400 uppercase tracking-wider font-bold">Unsupported Claim Flags</span>
                    </div>
                    {hallucinationFlags.length > 0 ? (
                        <div className="space-y-1.5">
                            {hallucinationFlags.slice(0, 5).map((flag, i) => (
                                <p key={i} className="text-[11px] text-rose-100/90 leading-relaxed font-mono">- {flag}</p>
                            ))}
                        </div>
                    ) : (
                        <p className="text-xs text-slate-500">No hallucination or unsupported-claim flags reported.</p>
                    )}
                </div>
                <div className={`p-4 rounded-xl border ${qualityNotes.length > 0 ? 'bg-sky-500/10 border-sky-500/30' : 'bg-black/20 border-white/5'}`}>
                    <div className="flex items-center gap-2 mb-2">
                        <Info size={14} className={qualityNotes.length > 0 ? 'text-sky-300' : 'text-slate-500'} />
                        <span className="text-[10px] text-slate-400 uppercase tracking-wider font-bold">Quality Notes</span>
                    </div>
                    {qualityNotes.length > 0 ? (
                        <div className="space-y-1.5">
                            {qualityNotes.slice(0, 5).map((note, i) => (
                                <p key={i} className="text-[11px] text-sky-100/90 leading-relaxed font-mono">- {note}</p>
                            ))}
                        </div>
                    ) : (
                        <p className="text-xs text-slate-500">No format or style notes reported.</p>
                    )}
                </div>
            </div>
        </div>

        {/* GENERAL SUMMARY — plain prose, no inline links (links shown in AI Diagnosis) */}
        {data.general_summary && (
            <div className="bg-slate-800/40 p-8 rounded-2xl border border-slate-600/30 backdrop-blur-sm">
                <h3 className="text-xl font-bold text-white mb-4 flex items-center gap-2">
                    <Search className="text-slate-400" size={22} />
                    General Summary
                    {data.general_summary_verified === false && (
                        <span className="ml-2 text-xs font-normal text-amber-400 bg-amber-400/10 px-2 py-0.5 rounded-full border border-amber-400/20">
                            unverified claims removed
                        </span>
                    )}
                </h3>
                <div>
                    {data.general_summary.split(/\n\n+/).map((para, pi) => (
                        <p key={pi} className={`text-slate-200 leading-relaxed text-base font-light${pi > 0 ? " mt-4" : ""}`}>
                            {para}
                        </p>
                    ))}
                </div>
            </div>
        )}

        <div className="flex items-center gap-2">
            <button onClick={() => setActiveTab("diagnosis")} className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${activeTab === "diagnosis" ? "bg-indigo-500/20 border-indigo-400/40 text-indigo-200" : "bg-white/5 border-white/10 text-slate-400"}`}>Diagnosis</button>
            <button onClick={() => setActiveTab("activation")} className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${activeTab === "activation" ? "bg-cyan-500/20 border-cyan-400/40 text-cyan-200" : "bg-white/5 border-white/10 text-slate-400"}`}>Activation</button>
        </div>

        {activeTab === "diagnosis" && (
        <div className="ai-diagnosis-section bg-slate-800/40 p-8 rounded-2xl border border-slate-600/30 backdrop-blur-sm">
            <h3 className="text-xl font-bold text-white mb-4 flex items-center gap-2">
            <ShieldCheck className="text-indigo-400" />
            AI Diagnosis
            </h3>
            <p className="text-slate-200 leading-relaxed whitespace-pre-line text-base font-light">
                <HighlightText text={data.summary_text} />
            </p>
            {data.evidence_based_risks && data.evidence_based_risks.length > 0 && (
                <div className="mt-5">
                    <p className="text-[11px] font-bold text-rose-300 uppercase tracking-wider mb-2">Evidence-Based Risks</p>
                    <ul className="space-y-2">
                        {data.evidence_based_risks.map((risk, i) => (<li key={i} className="text-sm text-slate-300 bg-black/20 border border-white/10 rounded-lg p-3">{risk}</li>))}
                    </ul>
                </div>
            )}
        </div>
        )}

        {/* REALITY CHECKLIST SECTION */}
        {data.reality_checklist && data.reality_checklist.length > 0 && (
            <div className="bg-white/5 p-6 rounded-2xl border border-white/10 backdrop-blur-md">
                <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                    <CheckCircle className="text-emerald-400" size={20} />
                    Forensic Reality Checklist
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {data.reality_checklist.map((item, idx) => {
                        let borderColor = "border-white/5";
                        let bgColor = "bg-black/20";
                        let icon = <div className="w-2 h-2 rounded-full bg-slate-600" />;
                        let textColor = "text-slate-400";
                        
                        if (item.found) {
                            if (item.sentiment === 'negative') {
                                borderColor = "border-rose-500/30";
                                bgColor = "bg-rose-500/10";
                                icon = <AlertTriangle size={14} className="text-rose-400" />;
                                textColor = "text-rose-200";
                            } else {
                                borderColor = "border-emerald-500/30";
                                bgColor = "bg-emerald-500/10";
                                icon = <CheckCircle size={14} className="text-emerald-400" />;
                                textColor = "text-emerald-200";
                            }
                        }

                        return (
                            <div key={idx} className={`flex items-center justify-between p-3 rounded-lg border ${borderColor} ${bgColor}`}>
                                <div className="flex items-center gap-3 overflow-hidden">
                                    <div className="shrink-0">{icon}</div>
                                    <div className="flex flex-col min-w-0">
                                        <span className={`text-xs font-medium ${textColor} truncate`}>
                                            {item.query || item.category}
                                        </span>
                                        <span className="text-[10px] text-slate-500 uppercase tracking-wider">{item.category}</span>
                                    </div>
                                </div>
                                {item.found && item.is_recent && (
                                    <span className="shrink-0 text-[9px] font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 px-1.5 py-0.5 rounded ml-2">
                                        RECENT
                                    </span>
                                )}
                            </div>
                        );
                    })}
                </div>
            </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div className="bg-white/5 p-6 rounded-2xl border border-white/10 backdrop-blur-md">
            <h3 className="text-lg font-bold text-white mb-6 flex items-center gap-2">
                <span className="w-1 h-6 bg-indigo-500 rounded-full"></span>
                Ambition Breakdown
            </h3>
            <div className="h-[300px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                <RadarChart cx="50%" cy="50%" outerRadius="80%" data={radarData}>
                    <PolarGrid stroke="#334155" />
                    <PolarAngleAxis dataKey="subject" tick={{ fill: '#94a3b8', fontSize: 11, fontWeight: 500 }} />
                    <PolarRadiusAxis angle={30} domain={[0, 100]} tick={false} axisLine={false} />
                    <Radar name="Ambition" dataKey="A" stroke="#818cf8" strokeWidth={2} fill="#6366f1" fillOpacity={0.3} />
                    <RechartsTooltip 
                        contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', color: '#f1f5f9', borderRadius: '8px' }}
                        itemStyle={{ color: '#818cf8' }}
                    />
                </RadarChart>
                </ResponsiveContainer>
            </div>
            </div>

            <div className="bg-white/5 p-6 rounded-2xl border border-white/10 backdrop-blur-md flex flex-col h-full">
            <h3 className="text-lg font-bold text-white mb-6 flex items-center gap-2">
                <span className="w-1 h-6 bg-emerald-500 rounded-full"></span>
                Deep Dive Findings
            </h3>

            <div className="flex-grow overflow-y-auto max-h-[450px] pr-2 space-y-3 custom-scrollbar">
                {(() => {
                    // Merge current + historical + foresight evidence, de-duped by URL
                    const currentLinks = (data.evidence_links || []).map(l => ({ ...l, _scan: 'current' }));
                    const seenUrls = new Set(currentLinks.map(l => l.url).filter(Boolean));

                    // Add validated foresight sources (origin: "foresight")
                    const foresightLinks = (data.foresight_links || [])
                        .filter(l => l.url && !seenUrls.has(l.url))
                        .map(l => {
                            seenUrls.add(l.url);
                            return { ...l, _scan: 'foresight' };
                        });

                    const historyLinks = [];
                    if (data.history && data.history.length > 0) {
                        for (const h of [...data.history].reverse()) {
                            const scanDate = h.date ? new Date(h.date).toLocaleDateString('en-US', { month: 'short', year: '2-digit' }) : '?';
                            for (const ev of (h.evidence || [])) {
                                if (ev.url && !seenUrls.has(ev.url)) {
                                    seenUrls.add(ev.url);
                                    historyLinks.push({ ...ev, _scan: scanDate });
                                }
                            }
                            // Include historical foresight sources
                            for (const fev of (h.foresight_evidence || [])) {
                                if (fev.url && !seenUrls.has(fev.url)) {
                                    seenUrls.add(fev.url);
                                    historyLinks.push({ ...fev, _scan: scanDate + ' foresight' });
                                }
                            }
                        }
                    }
                    const allLinks = [...currentLinks, ...foresightLinks, ...historyLinks];
                    const extractDomain = (url) => { try { return new URL(url).hostname.replace('www.', ''); } catch { return url; } };

                    if (allLinks.length === 0) {
                        return (
                            <div className="flex flex-col items-center justify-center h-full text-slate-600 border-2 border-dashed border-white/5 rounded-xl">
                                <AlertTriangle size={32} className="mb-2 opacity-50" />
                                <p className="text-sm">No specific evidence returned.</p>
                            </div>
                        );
                    }

                    return allLinks.map((link, idx) => (
                          <a
                              key={idx}
                              href={link.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="block p-4 rounded-xl bg-black/20 border border-white/5 hover:border-emerald-500/50 hover:bg-emerald-500/5 transition-all group"
                          >
                            <div className="flex items-start justify-between gap-2">
                                <div className="min-w-0 flex-grow">
                                    <h4 className="font-medium text-slate-200 group-hover:text-emerald-400 transition-colors text-sm line-clamp-1">
                                        <HighlightText text={link.title || extractDomain(link.url)} />
                                    </h4>
                                    {isForesightContext(link) ? (
                                        <div className="mt-1 space-y-1">
                                            <p className="text-[10px] text-slate-600 italic leading-relaxed">
                                                External trend/regulatory context — not proof of current company deployment.
                                            </p>
                                            {getSourceSays(link) && (
                                                <p className="text-[11px] text-slate-400 line-clamp-2 leading-relaxed">
                                                    <span className="text-teal-300/80 font-semibold">Source says:</span> {getSourceSays(link)}
                                                </p>
                                            )}
                                            {getPossibleRelevance(link) && (
                                                <p className="text-[11px] text-slate-500 line-clamp-2 leading-relaxed">
                                                    <span className="text-violet-300/80 font-semibold">Possible relevance:</span> {getPossibleRelevance(link)}
                                                </p>
                                            )}
                                        </div>
                                    ) : (link.source_quote || link.snippet) && (
                                        <p className="text-[11px] text-slate-400 mt-1 line-clamp-2 leading-relaxed">
                                            {link.source_quote || link.snippet}
                                        </p>
                                    )}
                                    <div className="flex items-center gap-2 mt-1">
                                        <span className="text-[10px] text-slate-500 font-mono truncate">{link.source || extractDomain(link.url)}</span>
                                        {link._scan === 'foresight' && (
                                            <span className="text-[9px] bg-teal-500/20 text-teal-400 px-1.5 py-0.5 rounded font-mono shrink-0">
                                                foresight
                                            </span>
                                        )}
                                        {isForesightContext(link) && (
                                            <span className="text-[9px] bg-violet-500/15 text-violet-300 px-1.5 py-0.5 rounded font-mono shrink-0" title="External trend/regulatory context — not proof of current company deployment">
                                                context-only
                                            </span>
                                        )}
                                        {isCompanySpecificEvidence(link) && (
                                            <span className="text-[9px] bg-emerald-500/15 text-emerald-300 px-1.5 py-0.5 rounded font-mono shrink-0" title="Direct company-specific evidence">
                                                company-fact
                                            </span>
                                        )}
                                        {getPromotionLabel(link) && (
                                            <span className={`text-[9px] px-1.5 py-0.5 rounded font-mono shrink-0 ${
                                                isAnalysisVerified(link) ? 'bg-teal-500/20 text-teal-300' :
                                                isVerificationConflict(link) ? 'bg-fuchsia-500/20 text-fuchsia-300' :
                                                link.promotion_status === 'excluded' ? 'bg-rose-500/20 text-rose-300' :
                                                'bg-slate-700/60 text-slate-400'
                                            }`} title={getVerificationReason(link)}>
                                                {getPromotionLabel(link)}
                                            </span>
                                        )}
                                        {link.claim_support_status && !isAnalysisVerified(link) && (
                                            <span className="text-[9px] bg-slate-700/50 text-slate-400 px-1.5 py-0.5 rounded font-mono shrink-0" title={getVerificationReason(link)}>
                                                {String(link.claim_support_status).replace(/_/g, ' ')}
                                            </span>
                                        )}
                                        {needsSourceRelevanceSplit(link) && (
                                            <span className="text-[9px] bg-amber-500/15 text-amber-300 px-1.5 py-0.5 rounded font-mono shrink-0" title="Context-only source_says contains company-specific interpretation; backend should split this into possible_relevance">
                                                needs split
                                            </span>
                                        )}
                                        {wasSourceSaysGenericized(link) && (
                                            <span className="text-[9px] bg-amber-500/15 text-amber-300 px-1.5 py-0.5 rounded font-mono shrink-0" title="Company name detected in source_says — text was genericized; company-specific interpretation moved to Possible relevance">
                                                relevance split
                                            </span>
                                        )}
                                        {link._scan !== 'current' && link._scan !== 'foresight' && (
                                            <span className="text-[9px] bg-slate-700/60 text-slate-400 px-1.5 py-0.5 rounded font-mono shrink-0">
                                                {link._scan}
                                            </span>
                                        )}
                                        {link.source_credibility && (
                                            <span className={`text-[9px] px-1.5 py-0.5 rounded font-mono shrink-0 ${
                                                link.source_credibility === 'high' ? 'bg-emerald-500/20 text-emerald-400' :
                                                link.source_credibility === 'medium' ? 'bg-amber-500/20 text-amber-400' :
                                                'bg-rose-500/20 text-rose-400'
                                            }`}>
                                                {link.source_credibility}
                                            </span>
                                        )}
                                        {link.verified === true && !link.source_credibility && (
                                            <span className="text-[9px] bg-slate-600/30 text-slate-400 px-1.5 py-0.5 rounded font-mono shrink-0">
                                                url_ok
                                            </span>
                                        )}
                                        {link.verified === false && (
                                            <span className="text-[9px] bg-rose-500/20 text-rose-400 px-1.5 py-0.5 rounded font-mono shrink-0">
                                                unverified
                                            </span>
                                        )}
                                    </div>
                              </div>
                              <ExternalLink size={16} className="text-slate-600 group-hover:text-emerald-400 mt-1 shrink-0" />
                            </div>
                          </a>
                    ));
                })()}
            </div>
            </div>
        </div>

        {activeTab === "activation" && (<>
        {/* STRATEGIC PITCH SECTION */}
        {data.pitch_text && (
            <div className="bg-white/5 p-6 rounded-2xl border border-white/10 backdrop-blur-md">
                <h3 className="text-lg font-bold text-white mb-3 flex items-center gap-2">
                    <Sparkles className="text-cyan-400" size={20} />
                    Strategic Outreach Pitch
                </h3>
                <div className="relative group">
                    <div className="bg-black/40 p-5 rounded-xl border border-white/10 font-mono text-xs text-slate-300 leading-relaxed">
                        {data.pitch_text.split(/\n\n+/).map((para, pi, arr) => (
                            <p key={pi} className={pi > 0 ? "mt-3" : ""}>
                                {pi === 0 ? "\u201c" : ""}{para}{pi === arr.length - 1 ? "\u201d" : ""}
                            </p>
                        ))}
                    </div>
                    <button
                        onClick={handleCopyPitch}
                        className={`absolute top-2 right-2 p-2 rounded transition-all shadow-md flex items-center gap-2 ${pitchCopied ? 'bg-emerald-600 text-white' : 'bg-white/10 hover:bg-cyan-600 hover:text-white text-slate-400'}`}
                        title="Copy Pitch to Clipboard"
                    >
                        {pitchCopied ? <Check size={14} /> : <Copy size={14} />}
                        {pitchCopied && <span className="text-[10px] font-bold">COPIED</span>}
                    </button>
                </div>
                <p className="text-[10px] text-slate-500 mt-3 flex items-center gap-1.5 opacity-70">
                     <Info size={12} />
                     Generated based on detected constraints: {(() => { const neg = (data.reality_checklist || []).filter((i: any) => i.sentiment === 'negative').slice(0, 3); return neg.length > 0 ? neg.map((i: any) => i.query).join(", ") : "General constraints"; })()}
                </p>
            </div>
        )}

        {/* WHY & WHAT PROPOSALS */}
        {data.why_proposals && data.why_proposals.length > 0 && data.what_proposals && data.what_proposals.length > 0 && (
            <div className="bg-white/5 p-6 rounded-2xl border border-white/10 backdrop-blur-md">
                <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                    <Zap className="text-amber-400" size={20} />
                    Proposed WHY & WHAT
                </h3>
                <div className="relative group">
                    <div className="space-y-4">
                        <div>
                            <p className="text-[11px] font-bold text-amber-400 uppercase tracking-wider mb-2">WHY — Desired Impact</p>
                            <div className="space-y-2">
                                {data.why_proposals.map((why, i) => (
                                    <div key={i} className="bg-black/30 p-3 rounded-lg border border-amber-500/15">
                                        <p className="text-xs text-slate-300 leading-relaxed">
                                            <span className="text-amber-400 font-bold mr-2">{i + 1}.</span>{why}
                                        </p>
                                    </div>
                                ))}
                            </div>
                        </div>
                        <div>
                            <p className="text-[11px] font-bold text-orange-400 uppercase tracking-wider mb-2">WHAT — Desired Effect</p>
                            <div className="space-y-2">
                                {data.what_proposals.map((what, i) => (
                                    <div key={i} className="bg-black/30 p-3 rounded-lg border border-orange-500/15">
                                        <p className="text-xs text-slate-300 leading-relaxed">
                                            <span className="text-orange-400 font-bold mr-2">{i + 1}.</span>{what}
                                        </p>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>
                    <button
                        onClick={handleCopyWhyWhat}
                        className={`absolute top-0 right-0 p-2 rounded transition-all shadow-md flex items-center gap-2 ${whyWhatCopied ? 'bg-emerald-600 text-white' : 'bg-white/10 hover:bg-amber-600 hover:text-white text-slate-400'}`}
                        title="Copy WHY & WHAT to Clipboard"
                    >
                        {whyWhatCopied ? <Check size={14} /> : <Copy size={14} />}
                        {whyWhatCopied && <span className="text-[10px] font-bold">COPIED</span>}
                    </button>
                </div>
                <p className="text-[10px] text-slate-500 mt-3 flex items-center gap-1.5 opacity-70">
                    <Info size={12} />
                    Proposals derived from the weakest Knowledge Base dimensions — intended as conversation starters, not promises.
                </p>
            </div>
        )}

        </>)}

        {/* === STRATEGIC FORESIGHT VISUALIZATIONS === */}
        {data.strategic_foresight?.dimension_forces?.length > 0 && (() => {
            const sf = data.strategic_foresight;
            const forceTypeColor = {
                'Probable/Emerging': { bg: 'bg-cyan-500/20', text: 'text-cyan-300', border: 'border-cyan-500/30' },
                'Plausible/Maturing': { bg: 'bg-emerald-500/20', text: 'text-emerald-300', border: 'border-emerald-500/30' },
                'Weakening/Declining': { bg: 'bg-amber-500/20', text: 'text-amber-300', border: 'border-amber-500/30' },
                'Preposterous/Black Swan': { bg: 'bg-rose-500/20', text: 'text-rose-300', border: 'border-rose-500/30' },
            };
            const fallbackColor = { bg: 'bg-slate-500/20', text: 'text-slate-300', border: 'border-slate-500/30' };
            const timeframeLabel = { '6-12mo': '6–12 mo', '12-24mo': '12–24 mo', '24-36mo': '24–36 mo' };
            const confidenceDots = { high: 3, medium: 2, low: 1 };

            return (
                <div className="space-y-5 mt-6">

                    {/* Validation Summary Badge — shows all sources analyzed */}
                    {sf.validation_summary && (() => {
                        // Count sources by credibility level across all forces
                        const credCounts = { high: 0, medium: 0, low: 0 };
                        const totalSources = sf.dimension_forces.reduce((acc, f) => {
                            const cred = f.source_credibility || 'low';
                            credCounts[cred] = (credCounts[cred] || 0) + 1;
                            let count = f.url_verified ? 1 : 0;
                            if (f.verified_supporting_sources?.length) {
                                count += f.verified_supporting_sources.length;
                                f.verified_supporting_sources.forEach(ss => {
                                    const ssCred = ss.source_credibility || 'medium';
                                    credCounts[ssCred] = (credCounts[ssCred] || 0) + 1;
                                });
                            }
                            return acc + count;
                        }, 0);
                        return (
                        <div className="flex flex-col gap-2 px-4 py-3 rounded-xl bg-white/5 border border-white/10">
                            <div className="flex flex-wrap items-center gap-3">
                                <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider ${
                                    sf.insufficient_evidence ? 'bg-rose-500/30 text-rose-300' :
                                    sf.validation_summary.quality === 'high' ? 'bg-emerald-500/20 text-emerald-400' :
                                    sf.validation_summary.quality === 'medium' ? 'bg-amber-500/20 text-amber-400' :
                                    'bg-rose-500/20 text-rose-400'
                                }`}>
                                    {sf.insufficient_evidence ? 'insufficient evidence' : `${sf.validation_summary.quality} quality`}
                                </span>
                                <span className="text-[10px] text-slate-400">
                                    {sf.validation_summary.total_forces} {sf.validation_summary.total_forces === 1 ? 'force' : 'forces'} analyzed · {sf.validation_summary.validated_forces} validated
                                </span>
                                {sf.validation_summary.total_verified_urls > 0 && (
                                    <span className="text-[10px] text-slate-500">
                                        · {sf.validation_summary.total_verified_urls} verified {sf.validation_summary.total_verified_urls === 1 ? 'URL' : 'URLs'}
                                    </span>
                                )}
                                {sf.validation_summary.flagged_forces > 0 && (
                                    <span className="text-[10px] text-amber-500/70">
                                        · {sf.validation_summary.flagged_forces} flagged
                                    </span>
                                )}
                            </div>
                            {/* Source credibility breakdown */}
                            <div className="flex flex-wrap items-center gap-2">
                                <span className="text-[9px] text-slate-500 uppercase tracking-wider font-mono">Sources:</span>
                                {credCounts.high > 0 && (
                                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 font-mono">
                                        {credCounts.high} high
                                    </span>
                                )}
                                {credCounts.medium > 0 && (
                                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 font-mono">
                                        {credCounts.medium} medium
                                    </span>
                                )}
                                {credCounts.low > 0 && (
                                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-rose-500/15 text-rose-400 font-mono">
                                        {credCounts.low} low
                                    </span>
                                )}
                            </div>
                        </div>
                        );
                    })()}
                    {sf.insufficient_evidence && (
                        <div className="px-4 py-2 rounded-lg bg-rose-500/10 border border-rose-500/20 text-[11px] text-rose-300/80">
                            Foresight evidence is below the minimum quality threshold. Sources could not be independently verified.
                            Conclusions should be treated with caution until corroborating evidence is found.
                        </div>
                    )}

                    {/* 2. Dimension Force Cards */}
                    <div className="grid grid-cols-1 gap-4">
                        {sf.dimension_forces.map((force, i) => {
                            const colors = forceTypeColor[force.type] || fallbackColor;
                            const dots = confidenceDots[force.confidence] || 1;
                            return (
                                <div key={i} className={`bg-white/5 p-5 rounded-2xl border ${colors.border} backdrop-blur-sm`}>
                                    <div className="flex flex-wrap items-center gap-2 mb-3">
                                        <span className={`text-[10px] px-2.5 py-1 rounded-full ${colors.bg} ${colors.text} font-semibold uppercase tracking-wider`}>
                                            {force.type || 'Unknown'}
                                        </span>
                                        <span className="text-[10px] px-2.5 py-1 rounded-full bg-white/10 text-slate-300 font-medium">
                                            {timeframeLabel[force.timeframe] || force.timeframe}
                                        </span>
                                        <span className="text-[10px] text-slate-500 ml-auto flex items-center gap-1" title={`Confidence: ${force.confidence || 'unknown'}`}>
                                            {[...Array(3)].map((_, d) => (
                                                <span key={d} className={`inline-block w-1.5 h-1.5 rounded-full ${d < dots ? 'bg-teal-400' : 'bg-slate-600'}`} />
                                            ))}
                                        </span>
                                    </div>
                                    <h4 className="text-sm font-bold text-white">{force.trend}</h4>
                                    <p className="text-[11px] text-teal-400/70 font-medium mt-0.5 mb-2">{force.kb_dimension}{force.fp_theme ? ` · ${force.fp_theme}` : ''}</p>
                                    <p className="text-xs text-slate-300 leading-relaxed">{force.impact_on_company}</p>
                                    <div className="mt-3 pt-3 border-t border-white/5 space-y-2">
                                        <div className="flex flex-wrap items-center gap-3">
                                            {force.source_url && (
                                                <a href={force.source_url} target="_blank" rel="noopener noreferrer" className="text-[10px] text-teal-400 hover:text-teal-300 flex items-center gap-1 transition-colors">
                                                    <ExternalLink size={10} /> Source
                                                </a>
                                            )}
                                            {force.source_type && (
                                                <span className="text-[10px] px-2 py-0.5 rounded bg-white/5 text-slate-400">{force.source_type}</span>
                                            )}
                                            {force.url_verified !== undefined && (
                                                <span className={`text-[9px] px-1.5 py-0.5 rounded font-mono ${force.url_verified ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'}`}>
                                                    {force.url_verified ? 'verified' : 'unverified'}
                                                </span>
                                            )}
                                            {force.source_credibility && (
                                                <span className={`text-[9px] px-1.5 py-0.5 rounded font-mono ${
                                                    force.source_credibility === 'high' ? 'bg-emerald-500/15 text-emerald-500' :
                                                    force.source_credibility === 'medium' ? 'bg-amber-500/15 text-amber-500' :
                                                    'bg-rose-500/15 text-rose-500'
                                                }`}>
                                                    {force.source_credibility}
                                                </span>
                                            )}
                                        </div>
                                        {/* Verified supporting sources (1-3 links per card) */}
                                        {force.verified_supporting_sources?.length > 0 && (
                                            <div className="flex flex-wrap gap-2">
                                                {force.verified_supporting_sources.slice(0, 3).map((ss, si) => (
                                                    <a key={si} href={ss.url} target="_blank" rel="noopener noreferrer"
                                                       className="text-[10px] text-slate-400 hover:text-teal-300 flex items-center gap-1 transition-colors"
                                                       title={ss.title || ss.url}>
                                                        <ExternalLink size={9} />
                                                        <span className="truncate max-w-[180px]">{ss.title || (() => { try { return new URL(ss.url).hostname.replace('www.', ''); } catch { return 'Source'; } })()}</span>
                                                    </a>
                                                ))}
                                            </div>
                                        )}
                                        {force.osint_validation && (
                                            <p className="text-[10px] text-slate-500 italic leading-relaxed">{force.osint_validation}</p>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>

                    {/* 3. Cross-Impact Chain */}
                    {sf.cross_impact_chain && sf.cross_impact_chain.trim() && (
                        <div className="bg-white/5 p-5 rounded-2xl border border-white/10">
                            <h4 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-4">Cross-Impact Chain</h4>
                            <div className="flex flex-col gap-0">
                                {sf.cross_impact_chain.split(/\s*(?:→|->)\s*/).filter(Boolean).map((node, i, arr) => (
                                    <div key={i} className="flex items-start gap-3">
                                        {/* Vertical connector line + dot */}
                                        <div className="flex flex-col items-center shrink-0" style={{ width: '20px' }}>
                                            <div className={`w-2.5 h-2.5 rounded-full border-2 ${i === 0 ? 'bg-teal-400 border-teal-400' : i === arr.length - 1 ? 'bg-cyan-400 border-cyan-400' : 'bg-transparent border-teal-500/60'}`} style={{ marginTop: '6px' }} />
                                            {i < arr.length - 1 && (
                                                <div className="w-px flex-grow bg-gradient-to-b from-teal-500/40 to-teal-500/10" style={{ minHeight: '20px' }} />
                                            )}
                                        </div>
                                        {/* Node content */}
                                        <div className={`text-xs leading-relaxed pb-3 ${i === 0 ? 'text-teal-200 font-semibold' : 'text-slate-300 font-medium'}`}>
                                            {i > 0 && <span className="text-teal-500/50 text-[10px] font-normal mr-1.5">{i === 1 ? 'impacts' : i === arr.length - 1 ? 'ultimately affecting' : 'which cascades to'}</span>}
                                            {node.trim()}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* 4. Micro-Scenarios */}
                    {sf.scenarios && (
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            {sf.scenarios.acceleration && (
                                <div className="bg-emerald-500/5 p-4 rounded-2xl border border-emerald-500/20">
                                    <div className="flex items-center gap-2 mb-2">
                                        <Zap size={14} className="text-emerald-400" />
                                        <h4 className="text-xs font-bold text-emerald-300 uppercase tracking-wider">If They Act Now</h4>
                                    </div>
                                    <p className="text-xs text-slate-300 leading-relaxed">{sf.scenarios.acceleration}</p>
                                </div>
                            )}
                            {sf.scenarios.status_quo && (
                                <div className="bg-amber-500/5 p-4 rounded-2xl border border-amber-500/20">
                                    <div className="flex items-center gap-2 mb-2">
                                        <RefreshCw size={14} className="text-amber-400" />
                                        <h4 className="text-xs font-bold text-amber-300 uppercase tracking-wider">Continue Unchanged</h4>
                                    </div>
                                    <p className="text-xs text-slate-300 leading-relaxed">{sf.scenarios.status_quo}</p>
                                </div>
                            )}
                            {sf.scenarios.disruption && (
                                <div className="bg-rose-500/5 p-4 rounded-2xl border border-rose-500/20">
                                    <div className="flex items-center gap-2 mb-2">
                                        <AlertTriangle size={14} className="text-rose-400" />
                                        <h4 className="text-xs font-bold text-rose-300 uppercase tracking-wider">Wild Card</h4>
                                    </div>
                                    <p className="text-xs text-slate-300 leading-relaxed">{sf.scenarios.disruption}</p>
                                </div>
                            )}
                        </div>
                    )}

                    {/* 5. Climate Exposure Panel */}
                    {sf.climate_exposure && (
                        <div className={`p-5 rounded-2xl border-l-4 backdrop-blur-sm ${
                            sf.climate_exposure.risk_level === 'critical' || sf.climate_exposure.risk_level === 'high'
                                ? 'bg-rose-900/20 border-rose-500'
                                : sf.climate_exposure.risk_level === 'medium'
                                    ? 'bg-amber-900/20 border-amber-500'
                                    : 'bg-emerald-900/20 border-emerald-500'
                        }`}>
                            <h4 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-2">
                                <Activity size={14} className={
                                    sf.climate_exposure.risk_level === 'critical' || sf.climate_exposure.risk_level === 'high'
                                        ? 'text-rose-400'
                                        : sf.climate_exposure.risk_level === 'medium' ? 'text-amber-400' : 'text-emerald-400'
                                } />
                                Climate Exposure — <span className="capitalize">{sf.climate_exposure.risk_level}</span> Risk
                            </h4>
                            {sf.climate_exposure.headline && (
                                <p className="text-sm text-slate-200 leading-relaxed mb-3">{sf.climate_exposure.headline}</p>
                            )}
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                {sf.climate_exposure.physical_risk && (
                                    <div className="bg-black/20 p-3 rounded-lg">
                                        <p className="text-[10px] text-slate-500 uppercase tracking-wider font-bold mb-1">Physical Risk</p>
                                        <p className="text-xs text-slate-300 leading-relaxed">{sf.climate_exposure.physical_risk}</p>
                                    </div>
                                )}
                                {sf.climate_exposure.transition_risk && (
                                    <div className="bg-black/20 p-3 rounded-lg">
                                        <p className="text-[10px] text-slate-500 uppercase tracking-wider font-bold mb-1">Transition Risk</p>
                                        <p className="text-xs text-slate-300 leading-relaxed">{sf.climate_exposure.transition_risk}</p>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            );
        })()}

        {/* === REPORT-ONLY SECTIONS (hidden in modal, visible in downloaded HTML) === */}
        {/* These sections are wrapped with report-only class which is display:none in the app
            but the downloaded HTML overrides it to display:block */}

        {data.detailed_report && (
            <div className="report-only-section" style={{ display: 'none' }}>
                <div className="bg-black/20 p-8 rounded-2xl border-l-4 border-violet-500 backdrop-blur-sm">
                    <h3 className="text-xl font-bold text-white mb-2 flex items-center gap-2">
                        <ShieldCheck className="text-violet-400" size={22} />
                        Detailed Forensic Report
                    </h3>
                    <p className="text-[10px] text-slate-500 mb-4 uppercase tracking-wider font-mono">
                        Deep diagnostic with evidence and stakeholder context
                    </p>
                    <div>
                        {data.detailed_report.split(/\n\n+/).map((block, pi) => {
                            const headingMatch = block.match(/^##\s+(.+)/);
                            if (headingMatch) {
                                return (
                                    <h4 key={pi} className={`text-base font-semibold text-violet-300 uppercase tracking-wide${pi > 0 ? " mt-6 mb-2" : " mb-2"}`}>
                                        {headingMatch[1]}
                                    </h4>
                                );
                            }
                            return (
                                <p key={pi} className={`text-slate-200 leading-relaxed text-sm font-light${pi > 0 ? " mt-4" : ""}`}>
                                    {block}
                                </p>
                            );
                        })}
                    </div>
                </div>
            </div>
        )}

        {/* DOMAIN FORCE DOSSIER — from Observatory */}
        {(() => {
          const domain = data.domain;
          const dossier = observatoryData?.domain_dossiers?.find((d: any) => d.domain === domain);
          if (!dossier || !dossier.text) return null;

          const archDist = dossier.archetype_distribution || {};
          const forces = dossier.forces_by_timescale || {};
          const ARCH_COLORS = {
            "Thriving": "text-emerald-300 bg-emerald-500/15 border-emerald-500/30",
            "Modernizing": "text-amber-300 bg-amber-500/15 border-amber-500/30",
            "Cash Cow": "text-blue-300 bg-blue-500/15 border-blue-500/30",
            "At Risk": "text-rose-300 bg-rose-500/15 border-rose-500/30",
          };

          return (
            <div className="bg-slate-800/40 rounded-2xl border border-slate-700/50 overflow-hidden backdrop-blur-sm">
              {/* Header */}
              <div className="px-8 pt-7 pb-5 border-b border-slate-700/30">
                <h3 className="text-xl font-bold text-white mb-1 flex items-center gap-2">
                  <Eye className="text-teal-400" size={22} />
                  Domain Force Dossier: {domain}
                </h3>
                <p className="text-[10px] text-slate-500 uppercase tracking-wider font-mono">
                  Strategic foresight analysis for the {domain} domain — from the Observatory
                </p>
                <p className="text-xs text-slate-400 mt-3 max-w-4xl leading-relaxed">
                  These domain metrics are derived from the Scanner’s analyzed company portfolio. They summarize observed company statuses and foresight pressure inside the Scanner dataset, rather than claiming to be a complete external market census.
                </p>

                {/* Domain stats */}
                <div className="flex flex-wrap gap-2 mt-4">
                  <span className="px-2.5 py-1 bg-slate-700/50 rounded-full text-[10px] font-mono text-slate-400">
                    {dossier.company_count} companies
                  </span>
                  {Object.entries(archDist).map(([k, v]: [string, any]) => (
                    <span key={k} className={`px-2.5 py-1 rounded-full text-[10px] font-bold ${ARCH_COLORS[k] || "text-slate-300 bg-slate-700/50 border-slate-600/30"}`}>
                      {k}: {v}
                    </span>
                  ))}
                </div>

                {/* Forces by timescale badges */}
                {(forces.act_now?.length > 0 || forces.prepare?.length > 0 || forces.position?.length > 0) && (
                  <div className="flex flex-wrap gap-2 mt-3">
                    {forces.act_now?.length > 0 && (
                      <span className="px-2.5 py-1 bg-rose-500/10 border border-rose-500/30 rounded-full text-[10px] font-mono text-rose-300">
                        Act Now: {forces.act_now.length} forces
                      </span>
                    )}
                    {forces.prepare?.length > 0 && (
                      <span className="px-2.5 py-1 bg-amber-500/10 border border-amber-500/30 rounded-full text-[10px] font-mono text-amber-300">
                        Prepare: {forces.prepare.length} forces
                      </span>
                    )}
                    {forces.position?.length > 0 && (
                      <span className="px-2.5 py-1 bg-teal-500/10 border border-teal-500/30 rounded-full text-[10px] font-mono text-teal-300">
                        Position: {forces.position.length} forces
                      </span>
                    )}
                  </div>
                )}
              </div>

              {/* Dossier essay text */}
              <div className="px-8 py-6">
                {dossier.text.split(/\n\n+/).map((block: string, pi: number) => {
                  const trimmed = block.trim();
                  if (!trimmed) return null;
                  // Skip raw title line (# Domain Force Dossier: ...)
                  if (/^#\s+Domain Force Dossier/i.test(trimmed)) return null;
                  // Skip horizontal rules (---)
                  if (/^-{3,}$/.test(trimmed)) return <hr key={pi} className="border-slate-700/40 my-6" />;
                  // Section headers (## SECTION ...)
                  const h2Match = trimmed.match(/^##\s+(.+)/);
                  if (h2Match) {
                    return (
                      <h4 key={pi} className={`text-[13px] font-bold text-teal-400 uppercase tracking-wide${pi > 0 ? " mt-8 mb-3" : " mb-3"}`}>
                        {h2Match[1]}
                      </h4>
                    );
                  }
                  const h3Match = trimmed.match(/^###\s+(.+)/);
                  if (h3Match) {
                    return (
                      <h5 key={pi} className="text-sm font-bold text-white mt-5 mb-2">
                        {h3Match[1]}
                      </h5>
                    );
                  }
                  // Bold text within paragraphs
                  const parts = trimmed.split(/(\*\*[^*]+\*\*)/);
                  return (
                    <p key={pi} className={`text-slate-300 leading-relaxed text-sm${pi > 0 ? " mt-4" : ""}`}>
                      {parts.map((seg: string, j: number) => {
                        if (seg.startsWith("**") && seg.endsWith("**")) {
                          return <strong key={j} className="text-white font-semibold">{seg.slice(2, -2)}</strong>;
                        }
                        return <span key={j}>{seg}</span>;
                      })}
                    </p>
                  );
                })}
              </div>

              {/* Observatory timestamp */}
              {observatoryData?.generated_at && (
                <div className="px-8 py-3 border-t border-slate-700/30 text-[9px] text-slate-600 font-mono">
                  Observatory analysis generated: {new Date(observatoryData.generated_at).toLocaleDateString()} | Model: {observatoryData.model}
                </div>
              )}
            </div>
          );
        })()}
      </div>
    </div>
  );
};

(window as any).CompanySummary = CompanySummary;
