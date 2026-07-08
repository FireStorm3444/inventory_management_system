use pyo3::prelude::*;
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, RwLock};

// ---------------------------------------------------------
// 1. Lightweight Data Transfer Object
// ---------------------------------------------------------
#[pyclass(from_py_object)]
#[derive(Clone)]
pub struct CatalogSearchResult {
    #[pyo3(get)]
    pub id: String,
    #[pyo3(get)]
    pub sku: String,
    #[pyo3(get)]
    pub name: String,
    #[pyo3(get)]
    pub price: f64,
}

#[pymethods]
impl CatalogSearchResult {
    #[new]
    fn new(id: String, sku: String, name: String, price: f64) -> Self {
        Self {
            id,
            sku,
            name,
            price,
        }
    }
}

// ---------------------------------------------------------
// 2. The Algorithmic Graph Node
// ---------------------------------------------------------
#[derive(Default)]
struct TrieNode {
    children: HashMap<char, TrieNode>,
    products: Vec<CatalogSearchResult>,
}

// ---------------------------------------------------------
// 3. The Thread-Safe Multi-Tenant Engine
// ---------------------------------------------------------
#[pyclass]
pub struct CatalogDiscoveryEngine {
    // Outer RwLock protects the tenant map.
    // Inner Arc<RwLock> isolates mutations so Tenant A doesn't block Tenant B.
    roots: RwLock<HashMap<String, Arc<RwLock<TrieNode>>>>,
}

#[pymethods]
impl CatalogDiscoveryEngine {
    #[new]
    fn new() -> Self {
        CatalogDiscoveryEngine {
            roots: RwLock::new(HashMap::new()),
        }
    }

    /// Indexes a product into the tenant's isolated Trie.
    fn insert(&self, tenant_id: String, search_term: String, product: CatalogSearchResult) {
        let term = search_term.trim().to_lowercase();
        if term.is_empty() {
            return;
        }

        // 1. Get or create the isolated tenant lock
        let tenant_lock = {
            let read_guard = self.roots.read().unwrap();
            if let Some(lock) = read_guard.get(&tenant_id) {
                Arc::clone(lock)
            } else {
                // Drop read guard BEFORE acquiring write guard to prevent deadlocks
                drop(read_guard);
                let mut write_guard = self.roots.write().unwrap();
                Arc::clone(
                    write_guard
                        .entry(tenant_id)
                        .or_insert_with(|| Arc::new(RwLock::new(TrieNode::default()))),
                )
            }
        };

        // 2. Traverse and mutate the graph
        let mut root = tenant_lock.write().unwrap();
        let mut current = &mut *root;

        for ch in term.chars() {
            current = current.children.entry(ch).or_insert_with(TrieNode::default);
        }
        current.products.push(product);
    }

    /// Sub-millisecond prefix resolution executed on bare metal.
    fn search(
        &self,
        py: Python,
        tenant_id: String,
        prefix: String,
        limit: usize,
    ) -> Vec<CatalogSearchResult> {
        let prefix = prefix.trim().to_lowercase();
        if prefix.is_empty() {
            return vec![];
        }

        // 1. Extract the tenant lock BEFORE dropping the GIL
        let tenant_lock = {
            let read_guard = self.roots.read().unwrap();
            match read_guard.get(&tenant_id) {
                Some(lock) => Arc::clone(lock),
                None => return vec![], // Tenant has no catalog mapped
            }
        };

        // 2. The GIL Release: Python threads are unblocked from here on.
        py.detach(move || {
            let root = tenant_lock.read().unwrap();
            let mut current = &*root;

            // Phase A: Traverse to the terminal node of the prefix
            for ch in prefix.chars() {
                match current.children.get(&ch) {
                    Some(node) => current = node,
                    None => return vec![],
                }
            }

            // Phase B: Execute Iterative DFS (Protects against stack overflows)
            let mut results = Vec::new();
            let mut seen_ids = HashSet::new();
            let mut stack = vec![current];

            while let Some(node) = stack.pop() {
                // Consume deduplicated products
                for prod in &node.products {
                    if !seen_ids.contains(&prod.id) {
                        seen_ids.insert(prod.id.clone());
                        results.push(prod.clone());
                        if results.len() >= limit {
                            return results; // Target acquired
                        }
                    }
                }
                // Queue children
                for child in node.children.values() {
                    stack.push(child);
                }
            }

            results
        })
    }
}

// ---------------------------------------------------------
// 4. Expose Module to Python
// ---------------------------------------------------------
#[pymodule]
fn rust_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<CatalogSearchResult>()?;
    m.add_class::<CatalogDiscoveryEngine>()?;
    Ok(())
}
