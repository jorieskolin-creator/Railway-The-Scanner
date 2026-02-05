import React, { useState, useEffect } from 'react';
import { AnalysisResult, ScannerInputData } from './types';
import { generateMockDashboardData, runMockScanner } from './services/mockScannerService';
import { TruthMatrix } from './components/TruthMatrix';
import { CompanyModal } from './components/CompanyModal';
import { ScannerInput } from './components/ScannerInput';
import { Activity, Search, ArrowUpDown, Plus, Loader2, X, Sparkles } from 'lucide-react';

const App: React.FC = () => {
  const [data, setData] = useState<AnalysisResult[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedCompany, setSelectedCompany] = useState<AnalysisResult | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isScannerOpen, setIsScannerOpen] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [sortConfig, setSortConfig] = useState<{ key: keyof AnalysisResult; direction: 'asc' | 'desc' } | null>({ key: 'reality_gap', direction: 'desc' });

  useEffect(() => {
    const fetchData = async () => {
      // Safely access environment variables
      const env = (import.meta as any).env || {};
      const blobUrl = env.VITE_BLOB_URL; 
      
      if (blobUrl) {
        try {
          const response = await fetch(blobUrl);
          if (response.ok) {
            const realData = await response.json();
            setData(realData);
            setLoading(false);
            return;
          }
        } catch (e) {
          console.error("Failed to fetch Blob data, falling back to mock", e);
        }
      }

      // Fallback
      console.warn("Using Mock Data");
      setTimeout(() => {
        const mockData = generateMockDashboardData();
        setData(mockData);
        setLoading(false);
      }, 1000);
    };

    fetchData();
  }, []);

  const handleRunScan = async (input: ScannerInputData) => {
    setIsScanning(true);
    
    // Safely access API URL
    const env = (import.meta as any).env || {};
    const apiUrl = env.VITE_API_URL; 

    try {
      let newResult: AnalysisResult;

      if (apiUrl) {
        const response = await fetch(`${apiUrl}/scan`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(input)
        });
        
        if (!response.ok) throw new Error("Scan failed");
        
        const json = await response.json();
        newResult = json.data;
      } else {
        console.warn("VITE_API_URL not set. Running simulation in browser.");
        await new Promise(r => setTimeout(r, 2000)); 
        newResult = await runMockScanner(input);
      }

      setData(prev => [newResult, ...prev]);
      setIsScannerOpen(false);
      setSelectedCompany(newResult);
      setIsModalOpen(true);
    } catch (error) {
      console.error(error);
      alert("Error running scan. Check console.");
    } finally {
      setIsScanning(false);
    }
  };

  const handleCompanyClick = (company: AnalysisResult) => {
    setSelectedCompany(company);
    setIsModalOpen(true);
  };

  const handleSort = (key: keyof AnalysisResult) => {
    let direction: 'asc' | 'desc' = 'desc';
    if (sortConfig && sortConfig.key === key && sortConfig.direction === 'desc') {
      direction = 'asc';
    }
    setSortConfig({ key, direction });
  };

  const filteredData = data.filter(item => 
    item.companyName.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const sortedData = [...filteredData].sort((a, b) => {
    if (!sortConfig) return 0;
    const aVal = a[sortConfig.key];
    const bVal = b[sortConfig.key];
    
    if (typeof aVal === 'string' && typeof bVal === 'string') {
        return sortConfig.direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
    }
    if (typeof aVal === 'number' && typeof bVal === 'number') {
        return sortConfig.direction === 'asc' ? aVal - bVal : bVal - aVal;
    }
    return 0;
  });

  return (
    <div className="min-h-screen flex flex-col font-sans relative overflow-x-hidden">
      
      {/* Background Decor */}
      <div className="fixed inset-0 z-0 bg-[#030712]">
        <div className="absolute inset-0 bg-grid-pattern opacity-20 pointer-events-none"></div>
        <div className="absolute top-0 left-1/4 w-96 h-96 bg-indigo-500/10 rounded-full blur-[128px] pointer-events-none"></div>
        <div className="absolute bottom-0 right-1/4 w-96 h-96 bg-emerald-500/10 rounded-full blur-[128px] pointer-events-none"></div>
      </div>

      {/* Header */}
      <header className="sticky top-0 z-40 w-full border-b border-white/5 bg-[#030712]/70 backdrop-blur-xl supports-[backdrop-filter]:bg-[#030712]/60">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="bg-gradient-to-br from-indigo-500 to-purple-600 p-2 rounded-lg text-white shadow-lg shadow-indigo-500/20">
              <Activity size={20} />
            </div>
            <h1 className="font-bold text-xl tracking-tight text-white hidden sm:block">
              Adaptability<span className="text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-purple-400">Scanner</span>
            </h1>
          </div>
          
          <div className="flex items-center gap-4 w-full max-w-md ml-4">
            <div className="relative w-full group">
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-500 group-focus-within:text-indigo-400 transition-colors">
                <Search size={16} />
              </div>
              <input
                type="text"
                placeholder="Search landscape..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-10 pr-4 py-2 bg-white/5 border border-white/10 rounded-lg text-sm text-slate-200 placeholder-slate-500 focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500/50 focus:bg-white/10 outline-none transition-all"
              />
            </div>
            <button 
              onClick={() => setIsScannerOpen(true)}
              className="hidden sm:flex items-center gap-2 px-4 py-2 bg-white text-slate-900 hover:bg-slate-200 font-semibold text-sm rounded-lg transition-all shadow-[0_0_20px_-5px_rgba(255,255,255,0.3)] hover:shadow-[0_0_25px_-5px_rgba(255,255,255,0.5)] whitespace-nowrap"
            >
              <Plus size={16} /> <span className="tracking-wide">NEW SCAN</span>
            </button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-grow py-8 px-4 sm:px-6 lg:px-8 max-w-7xl mx-auto w-full space-y-8 z-10 relative">
        
        {/* Top Row: Visual Overview */}
        <section className="space-y-4">
          <div className="flex justify-between items-center">
            <h2 className="text-2xl font-bold text-white flex items-center gap-2">
              <Sparkles className="text-yellow-400 w-5 h-5" /> Market Landscape
            </h2>
          </div>
          {loading ? (
             <div className="h-[400px] w-full bg-white/5 rounded-2xl border border-white/10 backdrop-blur-sm flex items-center justify-center">
                <Loader2 className="animate-spin text-indigo-500" size={48} />
             </div>
          ) : (
             <TruthMatrix data={data} onSelectCompany={handleCompanyClick} />
          )}
        </section>

        {/* Bottom Row: Data Table */}
        <section className="space-y-4">
          <div className="flex justify-between items-center">
            <h2 className="text-xl font-bold text-white">Index</h2>
            <span className="text-xs font-mono text-slate-500 uppercase tracking-wider bg-white/5 px-2 py-1 rounded border border-white/5">{filteredData.length} Entities</span>
          </div>

          <div className="bg-[#0B0F19]/80 border border-white/10 rounded-2xl overflow-hidden shadow-2xl backdrop-blur-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead className="text-xs text-slate-400 uppercase bg-white/5 border-b border-white/5">
                  <tr>
                    <th className="px-6 py-4 font-medium cursor-pointer hover:text-white transition-colors" onClick={() => handleSort('companyName')}>
                      <div className="flex items-center gap-2">Company <ArrowUpDown size={12} className="opacity-50" /></div>
                    </th>
                    <th className="px-6 py-4 font-medium cursor-pointer hover:text-white transition-colors" onClick={() => handleSort('intent_score')}>
                       <div className="flex items-center gap-2">Intent <ArrowUpDown size={12} className="opacity-50" /></div>
                    </th>
                    <th className="px-6 py-4 font-medium cursor-pointer hover:text-white transition-colors" onClick={() => handleSort('reality_score')}>
                       <div className="flex items-center gap-2">Reality <ArrowUpDown size={12} className="opacity-50" /></div>
                    </th>
                    <th className="px-6 py-4 font-medium cursor-pointer hover:text-white transition-colors" onClick={() => handleSort('reality_gap')}>
                       <div className="flex items-center gap-2">Gap <ArrowUpDown size={12} className="opacity-50" /></div>
                    </th>
                    <th className="px-6 py-4 font-medium cursor-pointer hover:text-white transition-colors" onClick={() => handleSort('lastUpdated')}>
                       <div className="flex items-center gap-2">Last Updated <ArrowUpDown size={12} className="opacity-50" /></div>
                    </th>
                    <th className="px-6 py-4 font-medium text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5 text-slate-400">
                  {loading ? (
                    [...Array(5)].map((_, i) => (
                      <tr key={i} className="animate-pulse">
                        <td className="px-6 py-4"><div className="h-4 bg-white/5 rounded w-32" /></td>
                        <td className="px-6 py-4"><div className="h-4 bg-white/5 rounded w-12" /></td>
                        <td className="px-6 py-4"><div className="h-4 bg-white/5 rounded w-12" /></td>
                        <td className="px-6 py-4"><div className="h-4 bg-white/5 rounded w-12" /></td>
                        <td className="px-6 py-4"><div className="h-4 bg-white/5 rounded w-24" /></td>
                        <td className="px-6 py-4"></td>
                      </tr>
                    ))
                  ) : sortedData.map((row) => (
                    <tr key={row.id} className="hover:bg-white/5 transition-colors group">
                      <td className="px-6 py-4 font-medium text-white group-hover:text-indigo-300 transition-colors">
                        {row.companyName}
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-2">
                            <div className="w-16 h-1.5 bg-slate-700 rounded-full overflow-hidden">
                                <div className="h-full bg-indigo-500 rounded-full" style={{ width: `${row.intent_score}%` }}></div>
                            </div>
                            <span className="text-xs font-mono">{row.intent_score}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-2">
                            <div className="w-16 h-1.5 bg-slate-700 rounded-full overflow-hidden">
                                <div className="h-full bg-emerald-500 rounded-full" style={{ width: `${row.reality_score}%` }}></div>
                            </div>
                            <span className="text-xs font-mono">{row.reality_score}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4 font-mono">
                        <span className={`${row.reality_gap > 20 ? 'text-rose-400' : row.reality_gap < -20 ? 'text-amber-400' : 'text-slate-300'}`}>
                          {row.reality_gap > 0 ? '+' : ''}{row.reality_gap}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-xs">
                        {new Date(row.lastUpdated).toLocaleDateString()}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <button 
                          onClick={() => handleCompanyClick(row)}
                          className="px-3 py-1.5 rounded-md bg-white/5 hover:bg-indigo-500/20 hover:text-indigo-300 border border-white/5 hover:border-indigo-500/50 text-xs font-medium transition-all"
                        >
                          View Report
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!loading && sortedData.length === 0 && (
              <div className="p-12 text-center text-slate-500 border-t border-white/5">
                No entities found matching "{searchQuery}"
              </div>
            )}
          </div>
        </section>

      </main>

      {/* Scanner Modal */}
      {isScannerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/80 backdrop-blur-md" onClick={() => !isScanning && setIsScannerOpen(false)} />
          <div className="relative w-full max-w-2xl bg-[#0B0F19] border border-white/10 rounded-2xl shadow-2xl overflow-hidden animate-in fade-in zoom-in duration-200 ring-1 ring-white/10">
             <div className="absolute top-4 right-4 z-10">
               {!isScanning && (
                <button onClick={() => setIsScannerOpen(false)} className="text-slate-400 hover:text-white transition-colors">
                  <X size={20} />
                </button>
               )}
             </div>
             
             {isScanning ? (
               <div className="p-16 flex flex-col items-center justify-center space-y-6 text-center">
                 <div className="relative">
                    <div className="w-16 h-16 border-4 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin"></div>
                    <div className="absolute inset-0 flex items-center justify-center">
                        <Activity className="text-indigo-400 animate-pulse" size={24} />
                    </div>
                 </div>
                 <div>
                   <h3 className="text-xl font-bold text-white tracking-tight">System Analyzing...</h3>
                   <div className="space-y-1 mt-3">
                    <p className="text-sm text-indigo-400 font-mono">>> Running Intent Scanner (NLP)</p>
                    <p className="text-sm text-emerald-400 font-mono">>> Running Reality Scanner (X-Ray)</p>
                   </div>
                 </div>
               </div>
             ) : (
               <ScannerInput onScan={handleRunScan} />
             )}
          </div>
        </div>
      )}

      {/* Pop-out Summary Component */}
      <CompanyModal 
        company={selectedCompany} 
        isOpen={isModalOpen} 
        onClose={() => setIsModalOpen(false)} 
      />

    </div>
  );
};

export default App;