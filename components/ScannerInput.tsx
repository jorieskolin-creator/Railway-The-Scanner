
const { useState } = (window as any).React;
const { Search, FileText, Globe, Zap, Link, AlertCircle, Database, Settings, Cpu } = (window as any).Lucide;

const ScannerInput = ({ onScan }) => {
  const [companyName, setCompanyName] = useState('');
  const [companyUrl, setCompanyUrl] = useState('');
  const [reportText, setReportText] = useState('');
  const [urlError, setUrlError] = useState('');
  const [showModels, setShowModels] = useState(false);
  const [modelList, setModelList] = useState([]);
  const [loadingModels, setLoadingModels] = useState(false);
  
  // NEW: Model Override State (Defaulting to Gemini 3 Pro as requested)
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [selectedModel, setSelectedModel] = useState('gemini-3-pro-preview');

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
    
    if (!companyName) {
      alert("Company Name is required.");
      return;
    }

    if (companyUrl && !validateUrl(companyUrl)) {
        setUrlError("Invalid URL format. Please include http:// or https://");
        return;
    }

    if (!companyUrl && !reportText) {
      alert("Please provide EITHER a Domain URL (for auto-scanning) OR paste Narrative Text manually.");
      return;
    }

    // Pass the selected model to the parent handler
    onScan({ companyName, companyUrl, reportText, modelId: selectedModel });
  };

  const fetchModels = async () => {
    if (showModels) {
        setShowModels(false);
        return;
    }
    setLoadingModels(true);
    setShowModels(true);
    try {
        const res = await fetch('/api/models');
        const json = await res.json();
        if (json.models) {
            setModelList(json.models);
        } else {
            setModelList(["Error fetching models: " + JSON.stringify(json)]);
        }
    } catch (e) {
        setModelList(["Network Error: " + e.message]);
    } finally {
        setLoadingModels(false);
    }
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
           <button type="button" onClick={() => setShowAdvanced(!showAdvanced)} className={`px-3 py-1.5 rounded border transition-all font-mono text-[10px] flex items-center gap-1 ${showAdvanced ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500/30' : 'bg-white/5 text-slate-400 border-transparent hover:bg-white/10'}`}>
            <Settings size={10} /> {showAdvanced ? 'HIDE OPTIONS' : 'AI MODEL'}
           </button>
           <button type="button" onClick={fetchModels} className="px-3 py-1.5 rounded bg-white/5 hover:bg-white/10 text-slate-400 hover:text-cyan-300 border border-transparent hover:border-cyan-500/30 transition-all font-mono text-[10px] flex items-center gap-1">
            <Database size={10} /> {showModels ? "HIDE LIST" : "CHECK SERVER"}
           </button>
        </div>
      </div>

      {showModels && (
          <div className="bg-black/30 p-4 border-b border-white/5 animate-in slide-in-from-top-2">
            <h4 className="text-xs font-bold text-white mb-2">Available Gemini Models (Server-Side)</h4>
            {loadingModels ? (
                <div className="text-xs text-slate-400">Querying Google Cloud...</div>
            ) : (
                <div className="grid grid-cols-2 gap-2 max-h-32 overflow-y-auto custom-scrollbar">
                    {modelList.map((m, i) => (
                        <div key={i} className="text-[10px] font-mono text-emerald-400 bg-emerald-950/30 px-2 py-1 rounded border border-emerald-900/50 truncate cursor-pointer hover:bg-emerald-900/50" onClick={() => { setSelectedModel(m); setShowModels(false); setShowAdvanced(true); }}>
                            {m}
                        </div>
                    ))}
                    {modelList.length === 0 && <div className="text-xs text-rose-400">No models found or API Key invalid.</div>}
                </div>
            )}
          </div>
      )}

      {showAdvanced && (
          <div className="bg-indigo-950/20 p-4 border-b border-indigo-500/20 animate-in slide-in-from-top-2 flex flex-col gap-2">
             <label className="text-[10px] font-bold uppercase tracking-widest text-indigo-300 flex items-center gap-2">
                <Cpu size={12} /> AI Model Override
             </label>
             <div className="flex flex-col sm:flex-row gap-2">
                 <input 
                    type="text" 
                    value={selectedModel} 
                    onChange={(e) => setSelectedModel(e.target.value)}
                    className="flex-1 bg-black/40 border border-indigo-500/30 rounded px-3 py-1.5 text-xs text-white font-mono placeholder-slate-600 focus:outline-none focus:border-indigo-400"
                    placeholder="e.g. gemini-3-pro-preview"
                 />
                 <div className="flex gap-1">
                    <button 
                        type="button"
                        onClick={() => setSelectedModel('gemini-3-pro-preview')} 
                        className={`px-2 py-1 rounded text-[10px] border transition-colors ${selectedModel === 'gemini-3-pro-preview' ? 'bg-indigo-600 text-white border-indigo-400' : 'bg-white/5 text-slate-400 border-white/5 hover:bg-white/10'}`}
                    >
                        3 Pro (Default)
                    </button>
                    <button 
                        type="button"
                        onClick={() => setSelectedModel('gemini-3-flash-preview')} 
                        className={`px-2 py-1 rounded text-[10px] border transition-colors ${selectedModel === 'gemini-3-flash-preview' ? 'bg-indigo-600 text-white border-indigo-400' : 'bg-white/5 text-slate-400 border-white/5 hover:bg-white/10'}`}
                    >
                        3 Flash
                    </button>
                    <button 
                        type="button"
                        onClick={() => setSelectedModel('gemini-2.5-pro')} 
                        className={`px-2 py-1 rounded text-[10px] border transition-colors ${selectedModel === 'gemini-2.5-pro' ? 'bg-indigo-600 text-white border-indigo-400' : 'bg-white/5 text-slate-400 border-white/5 hover:bg-white/10'}`}
                    >
                        2.5 Pro
                    </button>
                 </div>
             </div>
             <p className="text-[9px] text-slate-500">
                <span className="text-indigo-400 font-bold">Recommended:</span> Gemini 3 Pro for best reasoning. Flash for speed. 2.5 Pro for legacy stability.
             </p>
          </div>
      )}
      
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
            <span>Narrative Input (Ambition Source)</span>
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
