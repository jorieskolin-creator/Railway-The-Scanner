import React, { useState } from 'react';
import { ScannerInputData } from '../types';
import { Search, FileText, Globe, Zap } from 'lucide-react';

interface ScannerInputProps {
  onScan: (data: ScannerInputData) => void;
}

export const ScannerInput: React.FC<ScannerInputProps> = ({ onScan }) => {
  const [companyName, setCompanyName] = useState('');
  const [companyUrl, setCompanyUrl] = useState('');
  const [reportText, setReportText] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!companyName || !reportText) {
      alert("Please provide at least a Company Name and some Narrative Text.");
      return;
    }
    onScan({ companyName, companyUrl, reportText });
  };

  const loadSampleData = () => {
    setCompanyName("LegacyCorp International");
    setCompanyUrl("https://legacycorp.example.com");
    setReportText(`
      At LegacyCorp, we value Command and Control structures to ensure compliance across our Functional Silos. 
      Our Strategy is focused on Cost-cutting and defending our core Monolith assets. 
      While we talk about Digital Transformation, our approval chain requires significant handovers.
      We are exploring AI but our data remains in spreadsheets and on-premise servers.
      However, we aim for recurring revenue through vendor lock-in mechanisms.
    `);
  };

  const loadAdaptiveData = () => {
    setCompanyName("AdaptiveFlow Tech");
    setCompanyUrl("https://adaptiveflow.example.com");
    setReportText(`
      We organize around Value Streams with Stream-aligned teams that have Distributed Authority.
      Our platform strategy leverages an API-First approach to build a robust Ecosystem.
      We believe in Psychological Safety and Transparency to foster innovation.
      Our North Star is Value Creation through rapid experimentation.
      We use Cloud-native microservices and a Data Lake to power our Machine Learning initiatives.
    `);
  };

  return (
    <div className="bg-[#0B0F19]">
      <div className="bg-white/5 p-6 border-b border-white/5 flex justify-between items-center">
        <div>
            <h3 className="font-bold text-lg text-white flex items-center gap-2">
            <Search className="text-indigo-400" size={20} /> New Analysis
            </h3>
            <p className="text-xs text-slate-400 mt-1">Configure parameters for the BS Detector</p>
        </div>
        <div className="flex gap-2 text-xs">
           <button type="button" onClick={loadSampleData} className="px-3 py-1.5 rounded bg-white/5 hover:bg-rose-500/20 hover:text-rose-300 border border-transparent hover:border-rose-500/30 transition-all text-slate-400">
            Load "Legacy"
          </button>
           <button type="button" onClick={loadAdaptiveData} className="px-3 py-1.5 rounded bg-white/5 hover:bg-emerald-500/20 hover:text-emerald-300 border border-transparent hover:border-emerald-500/30 transition-all text-slate-400">
            Load "Adaptive"
          </button>
        </div>
      </div>
      
      <form onSubmit={handleSubmit} className="p-8 space-y-6">
        <div className="grid md:grid-cols-2 gap-6">
          <div className="space-y-2">
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-500">Target Entity</label>
            <div className="relative group">
              <input
                type="text"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                placeholder="e.g. Acme Corp"
                className="w-full pl-10 pr-4 py-3 bg-black/40 border border-white/10 rounded-lg text-white placeholder-slate-500 focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500/50 outline-none transition-all font-medium"
                required
              />
              <div className="absolute left-3 top-3.5 text-slate-500 group-focus-within:text-indigo-400 transition-colors">
                <Globe size={18} />
              </div>
            </div>
          </div>
          
          <div className="space-y-2">
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-500">Domain URL</label>
            <input
              type="url"
              value={companyUrl}
              onChange={(e) => setCompanyUrl(e.target.value)}
              placeholder="https://example.com"
              className="w-full px-4 py-3 bg-black/40 border border-white/10 rounded-lg text-white placeholder-slate-500 focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500/50 outline-none transition-all font-medium"
            />
          </div>
        </div>

        <div className="space-y-2">
          <label className="block text-xs font-semibold uppercase tracking-wider text-slate-500 flex justify-between">
            <span>Narrative Input (Intent)</span>
            <span className="text-slate-500 font-normal normal-case">Paste Annual Report / Strategy Page</span>
          </label>
          <div className="relative group">
             <textarea
              value={reportText}
              onChange={(e) => setReportText(e.target.value)}
              placeholder="Paste text here to analyze for 'Intent' keywords..."
              className="w-full pl-10 pr-4 py-3 h-48 bg-black/40 border border-white/10 rounded-lg text-white placeholder-slate-500 focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500/50 outline-none transition-all resize-none font-mono text-sm leading-relaxed custom-scrollbar"
              required
            />
            <div className="absolute left-3 top-3.5 text-slate-500 group-focus-within:text-indigo-400 transition-colors">
              <FileText size={18} />
            </div>
          </div>
          <p className="text-xs text-slate-500">
            Engine will parse for 50+ keywords across 6 dimensions of organizational design.
          </p>
        </div>

        <button
          type="submit"
          className="w-full py-4 bg-indigo-600 hover:bg-indigo-500 text-white font-bold rounded-lg shadow-[0_0_20px_-5px_rgba(79,70,229,0.5)] hover:shadow-[0_0_25px_-5px_rgba(79,70,229,0.7)] hover:-translate-y-0.5 transition-all duration-200 flex items-center justify-center gap-2 group"
        >
          <Zap size={18} className="text-indigo-200 group-hover:text-yellow-300 transition-colors" />
          Initiate Adaptability Scan
        </button>
      </form>
    </div>
  );
};