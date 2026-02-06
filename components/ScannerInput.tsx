
const { useState } = (window as any).React;
const { Search, FileText, Globe, Zap, Link, AlertCircle } = (window as any).Lucide;

const ScannerInput = ({ onScan }) => {
  const [companyName, setCompanyName] = useState('');
  const [companyUrl, setCompanyUrl] = useState('');
  const [reportText, setReportText] = useState('');
  const [urlError, setUrlError] = useState('');

  const validateUrl = (url) => {
    if (!url) return true;
    try {
      new URL(url);
      return true;
    } catch (e) {
      return false;
    }
  };

  const handleUrlChange = (e) => {
    const val = e.target.value;
    setCompanyUrl(val);
    
    if (val && !validateUrl(val)) {
      setUrlError("Invalid URL format. Please include http:// or https://");
    } else {
      setUrlError("");
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    
    // VALIDATION LOGIC:
    // 1. Company Name is always required.
    // 2. We need EITHER a URL (to auto-scrape) OR Text (to analyze manually).
    if (!companyName) {
      alert("Company Name is required.");
      return;
    }

    if (companyUrl && !validateUrl(companyUrl)) {
        // Double check on submit
        setUrlError("Invalid URL format. Please include http:// or https://");
        return;
    }

    if (!companyUrl && !reportText) {
      alert("Please provide EITHER a Domain URL (for auto-scanning) OR paste Narrative Text manually.");
      return;
    }

    onScan({ companyName, companyUrl, reportText });
  };

  const loadSampleData = () => {
    setCompanyName("Metsä Group");
    setCompanyUrl("https://www.metsagroup.com");
    setUrlError("");
    setReportText(""); 
    setReportText("We are committed to functional silos and strict command chains. Our strategy focuses on cost-cutting and maintaining our legacy assets.");
  };

  const loadAdaptiveData = () => {
    setCompanyName("Wärtsilä Oyj");
    setCompanyUrl("https://www.wartsila.com");
    setUrlError("");
    setReportText(""); 
  };

  return (
    <div className="bg-slate-900">
      <div className="bg-white/5 p-6 border-b border-white/5 flex justify-between items-center">
        <div>
            <h3 className="font-bold text-lg text-white flex items-center gap-2 tracking-tight">
            <Zap className="text-cyan-400" size={18} /> Configure Diagnosis
            </h3>
            <p className="text-[10px] text-slate-400 mt-1 uppercase tracking-widest font-semibold">Forensic Parameters (Finland Top 100)</p>
        </div>
        <div className="flex gap-2 text-xs">
           <button type="button" onClick={loadSampleData} className="px-3 py-1.5 rounded bg-white/5 hover:bg-rose-500/10 hover:text-rose-300 border border-transparent hover:border-rose-500/30 transition-all text-slate-400 font-mono text-[10px]">
            LOAD "LEGACY" (MANUAL TEXT)
          </button>
           <button type="button" onClick={loadAdaptiveData} className="px-3 py-1.5 rounded bg-white/5 hover:bg-emerald-500/10 hover:text-emerald-300 border border-transparent hover:border-emerald-500/30 transition-all text-slate-400 font-mono text-[10px]">
            LOAD "ADAPTIVE" (AUTO-SCAN)
          </button>
        </div>
      </div>
      
      <form onSubmit={handleSubmit} className="p-8 space-y-6">
        <div className="grid md:grid-cols-2 gap-6">
          <div className="space-y-2">
            <label className="block text-[10px] font-bold uppercase tracking-widest text-slate-500">Target Entity (FI)</label>
            <div className="relative group">
              <input
                type="text"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                placeholder="e.g. Kone, Neste, UPM"
                className="w-full pl-10 pr-4 py-3 bg-black/20 border border-white/10 rounded-lg text-white placeholder-slate-600 focus:ring-1 focus:ring-cyan-500/50 focus:border-cyan-500/50 outline-none transition-all font-medium text-sm"
                required
              />
              <div className="absolute left-3 top-3.5 text-slate-600 group-focus-within:text-cyan-400 transition-colors">
                <Globe size={16} />
              </div>
            </div>
          </div>
          
          <div className="space-y-2">
            <label className="block text-[10px] font-bold uppercase tracking-widest text-slate-500">Domain URL (Recommended)</label>
            <div className="relative group">
                <input
                  type="url"
                  value={companyUrl}
                  onChange={handleUrlChange}
                  placeholder="https://company.fi"
                  className={`w-full pl-10 pr-4 py-3 bg-black/20 border rounded-lg text-white placeholder-slate-600 focus:ring-1 outline-none transition-all font-medium text-sm ${urlError ? 'border-rose-500 focus:ring-rose-500/50' : 'border-white/10 focus:ring-cyan-500/50 focus:border-cyan-500/50'}`}
                />
                <div className={`absolute left-3 top-3.5 transition-colors ${urlError ? 'text-rose-500' : 'text-slate-600 group-focus-within:text-cyan-400'}`}>
                    <Link size={16} />
                </div>
            </div>
            {urlError && (
              <div className="flex items-center gap-1.5 text-rose-400 mt-1.5 animate-in slide-in-from-left-1 duration-200">
                <AlertCircle size={12} />
                <span className="text-[10px] font-medium">{urlError}</span>
              </div>
            )}
          </div>
        </div>

        <div className="space-y-2">
          <label className="block text-[10px] font-bold uppercase tracking-widest text-slate-500 flex justify-between items-center">
            <span>Narrative Input (Intent)</span>
            {companyUrl && !urlError ? (
                <span className="text-emerald-400 font-normal normal-case tracking-normal flex items-center gap-1">
                    <Zap size={10} /> Auto-Scrape Enabled (Leave empty to scan website)
                </span>
            ) : (
                <span className="text-rose-400 font-normal normal-case tracking-normal">
                    * Required if no URL provided
                </span>
            )}
          </label>
          <div className="relative group">
             <textarea
              value={reportText}
              onChange={(e) => setReportText(e.target.value)}
              placeholder={companyUrl ? "Leave empty to automatically scrape the homepage and Strategy pages..." : "Paste Annual Report, Strategy Text, or 'About Us' content here..."}
              className={`w-full pl-10 pr-4 py-3 h-32 bg-black/20 border rounded-lg text-white placeholder-slate-600 focus:ring-1 focus:ring-cyan-500/50 focus:border-cyan-500/50 outline-none transition-all resize-none font-mono text-xs leading-relaxed custom-scrollbar ${companyUrl && !urlError ? 'border-emerald-500/20' : 'border-white/10'}`}
            />
            <div className="absolute left-3 top-3.5 text-slate-600 group-focus-within:text-cyan-400 transition-colors">
              <FileText size={16} />
            </div>
          </div>
          <p className="text-[10px] text-slate-500 border-l-2 border-slate-700 pl-2">
            Scanner 1 will analyze this text (or scraped content) for <span className="text-cyan-400 font-bold">300+ markers</span> across 6 dimensions.
          </p>
        </div>

        <button
          type="submit"
          className="w-full py-4 bg-gradient-to-r from-cyan-600 to-indigo-600 hover:from-cyan-500 hover:to-indigo-500 text-white font-bold rounded-lg shadow-lg shadow-cyan-900/20 hover:-translate-y-0.5 transition-all duration-200 flex items-center justify-center gap-2 group text-sm tracking-wide"
        >
          <Zap size={16} className="text-cyan-200 group-hover:text-white transition-colors" />
          INITIATE DIAGNOSIS
        </button>
      </form>
    </div>
  );
};

(window as any).ScannerInput = ScannerInput;
