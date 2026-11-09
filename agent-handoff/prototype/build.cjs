const fs=require('node:fs');const path=require('node:path');
const root=__dirname,out=path.join(root,'dist');fs.mkdirSync(out,{recursive:true});
const pages={'index.html':'home','00-site-overview.html':'gantt','01-hq-review.html':'review','02-field-submission.html':'field','03-weekly-report.html':'report'};
for(const [file,name] of Object.entries(pages)){
  const html=fs.readFileSync(path.join(root,'src/templates',file),'utf8')
    .replace('</head>','<link rel="stylesheet" href="fonts.css"><link rel="stylesheet" href="mobile-fixes.css"><link rel="stylesheet" href="navigation.css"><link rel="stylesheet" href="scene.css"><script src="assets/site-catalog.js"></script><script src="site-summary.js"></script><script src="ui.js"></script><script src="task-details.js"></script><script src="navigation.js"></script></head>')
    .replace('<script src="chat.js">','<script src="pages/'+name+'-adapter.js"></script><script src="chat.js">')
    .replace('</head>','<link rel="stylesheet" href="review-tags.css"></head>');
  fs.writeFileSync(path.join(out,file),html);
}
for(const file of fs.readdirSync(path.join(root,'src')))if(/\.(js|css)$/.test(file))fs.copyFileSync(path.join(root,'src',file),path.join(out,file));
fs.cpSync(path.join(root,'src/pages'),path.join(out,'pages'),{recursive:true});
fs.cpSync(path.join(root,'assets'),path.join(out,'assets'),{recursive:true});
console.log('Built approved views → dist (5 pages)');
