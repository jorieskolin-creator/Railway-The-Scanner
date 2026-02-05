import React, { useState, useEffect } from 'react';
import { AnalysisResult } from './types';
import { generateMockDashboardData } from './services/mockScannerService';
import { TruthMatrix } from './components/TruthMatrix';
import { CompanyModal } from './components/CompanyModal';
import { Activity, Search, ArrowUpDown, Plus } from 'lucide-react';

const App: React.FC = () => {
  const [data, setData] = useState<AnalysisResult[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedCompany, setSelectedCompany] = useState<AnalysisResult | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [sortConfig, setSortConfig] = useState<{ key: keyof AnalysisResult; direction: 'asc' | 'desc' } | null>({ key: 'reality_gap', direction: 'desc' });

  useEffect(() => {
    // Simulate Fetching Data from Blob Storage
    setTimeout(() => {
      const mockData = generateMockDashboardData();
      setData(mockData);
      setLoading(false);
    }, 1000);
  }, []);

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
    <div className="min-h-screen flex flex-col font-sans bg-slate-950 text-slate-100">
      
      {/* Header */}
      <header className="bg-slate-900 border-b border-slate-800 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="bg-indigo-600 p-1.5 rounded-lg text-white">
              <Activity size={20} />
            </div>
            <h1 className="font-bold text-xl tracking-tight text-white hidden sm:block">
              Adaptability<span className="text-indigo-500">Scanner</span>
            </h1>
          </div>
          
          <div className="flex items-center gap-4 w-full max-w-md ml-4">
            <div className="relative w-full">
              <input
                type="text"
                placeholder="Search companies..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-10 pr-4 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-200 focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none transition-all placeholder-slate-500"
              />
              <div className="absolute left-3 top-2.5 text-slate-500">
                <Search size={16} />
              </div>
            </div>
            <button className="hidden sm:flex items-center gap-2 px-3 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors whitespace-nowrap">
              <Plus size={16} /> New Scan
            </button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-grow py-8 px-4 sm:px-6 lg:px-8 max-w-7xl mx-auto w-full space-y-8">
        
        {/* Top Row: Visual Overview */}
        <section>
          <div className="flex justify-between items-end mb-4">
            <h2 className="text-2xl font-bold text-white">Market Landscape</h2>
          </div>
          {loading ? (
             <div className="h-[400px] w-full bg-slate-900 rounded-xl border border-slate-800 animate-pulse" />
          ) : (
             <TruthMatrix data={data} onSelectCompany={handleCompanyClick} />
          )}
        </section>

        {/* Bottom Row: Data Table */}
        <section>
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-xl font-bold text-white">Adaptability Index</h2>
            <span className="text-sm text-slate-400">{filteredData.length} Companies tracked</span>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-lg">
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left text-slate-400">
                <thead className="text-xs text-slate-400 uppercase bg-slate-900/50 border-b border-slate-800">
                  <tr>
                    <th className="px-6 py-4 font-medium cursor-pointer hover:text-white" onClick={() => handleSort('companyName')}>
                      <div className="flex items-center gap-1">Company <ArrowUpDown size={14} /></div>
                    </th>
                    <th className="px-6 py-4 font-medium cursor-pointer hover:text-white" onClick={() => handleSort('intent_score')}>
                       <div className="flex items-center gap-1">Intent Score <ArrowUpDown size={14} /></div>
                    </th>
                    <th className="px-6 py-4 font-medium cursor-pointer hover:text-white" onClick={() => handleSort('reality_score')}>
                       <div className="flex items-center gap-1">Reality Score <ArrowUpDown size={14} /></div>
                    </th>
                    <th className="px-6 py-4 font-medium cursor-pointer hover:text-white" onClick={() => handleSort('reality_gap')}>
                       <div className="flex items-center gap-1">Reality Gap <ArrowUpDown size={14} /></div>
                    </th>
                    <th className="px-6 py-4 font-medium cursor-pointer hover:text-white" onClick={() => handleSort('lastUpdated')}>
                       <div className="flex items-center gap-1">Last Updated <ArrowUpDown size={14} /></div>
                    </th>
                    <th className="px-6 py-4 font-medium text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {loading ? (
                    [...Array(5)].map((_, i) => (
                      <tr key={i} className="animate-pulse">
                        <td className="px-6 py-4"><div className="h-4 bg-slate-800 rounded w-32" /></td>
                        <td className="px-6 py-4"><div className="h-4 bg-slate-800 rounded w-12" /></td>
                        <td className="px-6 py-4"><div className="h-4 bg-slate-800 rounded w-12" /></td>
                        <td className="px-6 py-4"><div className="h-4 bg-slate-800 rounded w-12" /></td>
                        <td className="px-6 py-4"><div className="h-4 bg-slate-800 rounded w-24" /></td>
                        <td className="px-6 py-4"></td>
                      </tr>
                    ))
                  ) : sortedData.map((row) => (
                    <tr key={row.id} className="bg-slate-900 hover:bg-slate-800/50 transition-colors group">
                      <td className="px-6 py-4 font-medium text-white">
                        {row.companyName}
                      </td>
                      <td className="px-6 py-4">
                        <span className="inline-block px-2 py-1 rounded bg-indigo-500/10 text-indigo-400 text-xs font-semibold">
                          {row.intent_score}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        <span className="inline-block px-2 py-1 rounded bg-emerald-500/10 text-emerald-400 text-xs font-semibold">
                          {row.reality_score}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        <span className={`font-bold ${row.reality_gap > 20 ? 'text-red-400' : row.reality_gap < -20 ? 'text-yellow-400' : 'text-slate-300'}`}>
                          {row.reality_gap > 0 ? '+' : ''}{row.reality_gap}
                        </span>
                        {row.reality_gap > 30 && <span className="ml-2 text-xs text-red-500">⚠️</span>}
                      </td>
                      <td className="px-6 py-4">
                        {new Date(row.lastUpdated).toLocaleDateString()}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <button 
                          onClick={() => handleCompanyClick(row)}
                          className="text-indigo-400 hover:text-indigo-300 text-xs font-medium hover:underline"
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
              <div className="p-12 text-center text-slate-500">
                No companies found matching "{searchQuery}"
              </div>
            )}
          </div>
        </section>

      </main>

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
