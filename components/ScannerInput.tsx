import React, { useState } from 'react';
import { ScannerInputData } from '../types';
import { Search, FileText, Globe } from 'lucide-react';

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
    <div className="bg-white rounded-2xl shadow-xl border border-slate-200 overflow-hidden">
      <div className="bg-slate-50 p-4 border-b border-slate-100 flex justify-between items-center">
        <h3 className="font-semibold text-slate-700 flex items-center gap-2">
          <Search size={18} /> New Analysis
        </h3>
        <div className="flex gap-2 text-xs">
           <button type="button" onClick={loadSampleData} className="text-indigo-600 hover:underline">
            Load "Bad" Sample
          </button>
          <span className="text-slate-300">|</span>
           <button type="button" onClick={loadAdaptiveData} className="text-emerald-600 hover:underline">
            Load "Good" Sample
          </button>
        </div>
      </div>
      
      <form onSubmit={handleSubmit} className="p-6 md:p-8 space-y-6">
        <div className="grid md:grid-cols-2 gap-6">
          <div className="space-y-2">
            <label className="block text-sm font-medium text-slate-700">Company Name</label>
            <div className="relative">
              <input
                type="text"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                placeholder="e.g. Acme Corp"
                className="w-full pl-10 pr-4 py-3 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none transition-all"
                required
              />
              <div className="absolute left-3 top-3.5 text-slate-400">
                <Globe size={18} />
              </div>
            </div>
          </div>
          
          <div className="space-y-2">
            <label className="block text-sm font-medium text-slate-700">Company URL</label>
            <input
              type="url"
              value={companyUrl}
              onChange={(e) => setCompanyUrl(e.target.value)}
              placeholder="https://example.com"
              className="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none transition-all"
            />
          </div>
        </div>

        <div className="space-y-2">
          <label className="block text-sm font-medium text-slate-700 flex justify-between">
            <span>Narrative Analysis Text</span>
            <span className="text-slate-400 font-normal">Paste Annual Report / Strategy Page</span>
          </label>
          <div className="relative">
             <textarea
              value={reportText}
              onChange={(e) => setReportText(e.target.value)}
              placeholder="Paste text here to analyze for 'Intent' keywords..."
              className="w-full pl-10 pr-4 py-3 h-48 bg-slate-50 border border-slate-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none transition-all resize-none font-mono text-sm"
              required
            />
            <div className="absolute left-3 top-3.5 text-slate-400">
              <FileText size={18} />
            </div>
          </div>
          <p className="text-xs text-slate-500">
            The scanner will look for keywords related to Operating Models, Values, and Strategy.
          </p>
        </div>

        <button
          type="submit"
          className="w-full py-4 bg-indigo-600 hover:bg-indigo-700 text-white font-bold rounded-lg shadow-lg hover:shadow-xl hover:-translate-y-0.5 transition-all duration-200 flex items-center justify-center gap-2"
        >
          Run Adaptability Scan
        </button>
      </form>
    </div>
  );
};
