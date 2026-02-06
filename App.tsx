
const { useState, useEffect, useMemo } = (window as any).React;
const { Zap, LayoutDashboard, Search, X, Loader2, ArrowUpRight } = (window as any).Lucide;

// Components
const ScannerInput = (window as any).ScannerInput;
const TruthMatrix = (window as any).TruthMatrix;
const CompanyModal = (window as any).CompanyModal;

// Services
const runMockScanner = (window as any).runMockScanner;
const generateMockDashboardData = (window as any).generateMockDashboardData;
const generateSynthesis = (window as any).generateSynthesis;

const App = () => {
  // State
  const [data, setData] = useState([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [isScanning, setIsScanning] = useState(false);
  const [selectedCompany, setSelectedCompany] = useState(null);

  // Initial Load
  useEffect(() => {
    if (generateMockDashboardData) {
        setData(generateMockDashboardData());
    }
  }, []);

  // Filter Logic
  const filteredData = useMemo(() => {
    if (!searchQuery) return data;
    return data.filter(c => 
        c.companyName.toLowerCase().includes(searchQuery.toLowerCase())
    );
  }, [data, searchQuery]);

  // Handlers
  const handleScan = async (input) => {
      setIsScanning(true);
      try {
          // Simulate network delay for effect
          await new Promise(r => setTimeout(r, 1000));
          
          const result = await runMockScanner(input);
          
          // Get AI Synthesis
          const summary = await generateSynthesis(result, input.companyName);
          result.summary_text = summary;

          setData(prev => [result, ...prev]);
      } catch (e) {
          console.error("Scan Failed:", e);
      } finally {
          setIsScanning(false);
      }
  };

  const handleCompanyUpdate = (updatedCompany) => {
      setData(prev => prev.map(c => c.id === updatedCompany.id ? updatedCompany : c));
      setSelectedCompany(updatedCompany);
  };

  return (
    <div className="min-h-screen bg-[#020617] text-slate-300 font-sans selection:bg-cyan-500/30">
        {/* Navbar */}
        <header className="sticky top-0 z-30 bg-[#020617]/80 backdrop-blur-md border-b border-white/10">
            <div className="max-w-7xl mx-auto px-6 py-4 flex justify-between items-center">
                <div className="flex items-center gap-3">
                    <div className="p-2 bg-cyan-900/20 rounded-lg border border-cyan-500/20">
                        <Zap className="text-cyan-400" size={20} fill="currentColor" fillOpacity={0.3} />
                    </div>
                    <div>
                        <h1 className="text-xl font-black text-white tracking-tighter leading-none">
                            AGILE<span className="text-cyan-400">TRUTH</span>
                        </h1>
                        <p className="text-[10px] text-slate-500 uppercase tracking-widest font-mono">
                            Forensic Reality Scanner
                        </p>
                    </div>
                </div>
                <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-emerald-950/30 border border-emerald-500/20 text-[10px] font-bold text-emerald-400 uppercase tracking-widest">
                    <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                    System Active
                </div>
            </div>
        </header>

        <main className="max-w-7xl mx-auto px-6 py-8 space-y-10">
            
            {/* Upper Section */}
            <div className="grid lg:grid-cols-3 gap-8">
                {/* Input Area */}
                <div className="lg:col-span-1 space-y-6">
                    <div className="bg-[#0f172a] rounded-2xl border border-white/10 shadow-2xl overflow-hidden">
                        <ScannerInput onScan={handleScan} />
                    </div>
                    {isScanning && (
                        <div className="bg-cyan-950/20 border border-cyan-500/30 rounded-xl p-4 flex items-center gap-4 animate-pulse">
                            <Loader2 className="animate-spin text-cyan-400" size={24} />
                            <div>
                                <h4 className="text-cyan-300 font-bold text-sm">Processing Diagnosis</h4>
                                <p className="text-cyan-500/70 text-xs">Analyzing narrative vs. reality...</p>
                            </div>
                        </div>
                    )}
                </div>

                {/* Matrix Area */}
                <div className="lg:col-span-2">
                    <div className="bg-[#0f172a]/50 backdrop-blur-sm rounded-2xl border border-white/10 p-6 h-full flex flex-col">
                        <div className="flex justify-between items-center mb-6">
                            <h2 className="text-lg font-bold text-white flex items-center gap-2">
                                <LayoutDashboard className="text-indigo-400" size={18} />
                                Truth Matrix
                            </h2>
                            <span className="text-xs font-mono text-slate-500 border border-white/10 px-2 py-1 rounded">
                                N = {filteredData.length}
                            </span>
                        </div>
                        <div className="flex-grow">
                             <TruthMatrix data={filteredData} onSelectCompany={setSelectedCompany} />
                        </div>
                    </div>
                </div>
            </div>

            {/* List Section */}
            <section className="space-y-4">
              <div className="flex justify-between items-center">
                <div className="flex items-center gap-4">
                    <h2 className="text-lg font-bold text-white tracking-tight">Forensic Index</h2>
                    {/* Search */}
                    <div className="relative group">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 group-focus-within:text-cyan-400 transition-colors" size={14} />
                        <input 
                            type="text"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)} 
                            placeholder="Filter entities..."
                            className="bg-[#0f172a] border border-white/10 rounded-full pl-9 pr-4 py-1.5 text-xs text-white placeholder-slate-600 focus:ring-1 focus:ring-cyan-500/50 outline-none w-48 transition-all focus:w-64"
                        />
                    </div>
                    {searchQuery && (
                        <button
                            onClick={() => setSearchQuery('')}
                            className="flex items-center gap-1.5 px-3 py-1 text-[10px] font-bold uppercase tracking-wider text-rose-300 bg-rose-500/10 border border-rose-500/20 rounded-md hover:bg-rose-500/20 hover:text-rose-200 transition-all shadow-lg shadow-rose-900/20 animate-in fade-in zoom-in duration-200"
                        >
                            <X size={12} /> Clear Search: "{searchQuery}"
                        </button>
                    )}
                </div>
                <span className="text-[10px] font-mono text-indigo-200 uppercase tracking-wider bg-[#0f172a]/50 px-2 py-1 rounded border border-indigo-500/20">{filteredData.length} Entities</span>
              </div>

              <div className="bg-[#0f172a]/70 border border-white/10 rounded-2xl overflow-hidden shadow-2xl backdrop-blur-xl">
                 <div className="overflow-x-auto">
                    <table className="w-full text-left">
                        <thead>
                            <tr className="bg-black/20 text-[10px] uppercase tracking-widest text-slate-500 font-bold">
                                <th className="px-6 py-4">Entity</th>
                                <th className="px-6 py-4 text-center">Intent</th>
                                <th className="px-6 py-4 text-center">Reality</th>
                                <th className="px-6 py-4 text-center">Gap</th>
                                <th className="px-6 py-4 text-right">Last Scan</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-white/5 text-sm text-slate-400">
                            {filteredData.map(company => (
                                <tr 
                                    key={company.id} 
                                    onClick={() => setSelectedCompany(company)}
                                    className="hover:bg-white/5 transition-colors cursor-pointer group"
                                >
                                    <td className="px-6 py-4 font-medium text-white group-hover:text-cyan-300 transition-colors">
                                        <div className="flex items-center gap-2">
                                            {company.companyName}
                                            <ArrowUpRight size={12} className="opacity-0 group-hover:opacity-100 transition-opacity" />
                                        </div>
                                    </td>
                                    <td className="px-6 py-4 text-center font-mono">{company.intent_score}</td>
                                    <td className="px-6 py-4 text-center font-mono">{company.reality_score}</td>
                                    <td className="px-6 py-4 text-center">
                                        <span className={`px-2 py-1 rounded text-xs font-bold border ${
                                            company.reality_gap > 15 ? 'bg-rose-500/10 text-rose-400 border-rose-500/20' :
                                            company.reality_gap < -15 ? 'bg-amber-500/10 text-amber-400 border-amber-500/20' :
                                            'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                                        }`}>
                                            {company.reality_gap > 0 ? '+' : ''}{company.reality_gap}
                                        </span>
                                    </td>
                                    <td className="px-6 py-4 text-right font-mono text-xs opacity-60">
                                        {new Date(company.lastUpdated).toLocaleDateString()}
                                    </td>
                                </tr>
                            ))}
                            {filteredData.length === 0 && (
                                <tr>
                                    <td colSpan={5} className="px-6 py-12 text-center text-slate-600">
                                        No results found for "{searchQuery}"
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                 </div>
              </div>
            </section>
        </main>

        <CompanyModal 
            company={selectedCompany} 
            isOpen={!!selectedCompany} 
            onClose={() => setSelectedCompany(null)} 
            onUpdate={handleCompanyUpdate}
        />
    </div>
  );
};

(window as any).App = App;
