use pyo3::prelude::*;
use pyo3::types::PyDict;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

fn new_dict(py: Python<'_>) -> Bound<'_, PyDict> {
    PyDict::new_bound(py)
}

// ---------------------------------------------------------------------------
// MovieLink
// ---------------------------------------------------------------------------

#[pyclass(name = "RustMovieLink")]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MovieLink {
    #[pyo3(get, set)]
    pub name: String,
    #[pyo3(get, set)]
    pub href: String,
}

#[pymethods]
impl MovieLink {
    #[new]
    #[pyo3(signature = (name, href))]
    fn new(name: String, href: String) -> Self {
        Self { name, href }
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = new_dict(py);
        dict.set_item("name", &self.name)?;
        dict.set_item("href", &self.href)?;
        Ok(dict)
    }

    fn __repr__(&self) -> String {
        format!("RustMovieLink(name='{}', href='{}')", self.name, self.href)
    }
}

// ---------------------------------------------------------------------------
// MagnetInfo
// ---------------------------------------------------------------------------

#[pyclass(name = "RustMagnetInfo")]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MagnetInfo {
    #[pyo3(get, set)]
    pub href: String,
    #[pyo3(get, set)]
    pub name: String,
    #[pyo3(get, set)]
    pub tags: Vec<String>,
    #[pyo3(get, set)]
    pub size: String,
    #[pyo3(get, set)]
    pub timestamp: String,
}

#[pymethods]
impl MagnetInfo {
    #[new]
    #[pyo3(signature = (href, name, tags=vec![], size=String::new(), timestamp=String::new()))]
    fn new(href: String, name: String, tags: Vec<String>, size: String, timestamp: String) -> Self {
        Self {
            href,
            name,
            tags,
            size,
            timestamp,
        }
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = new_dict(py);
        dict.set_item("href", &self.href)?;
        dict.set_item("name", &self.name)?;
        dict.set_item("tags", &self.tags)?;
        dict.set_item("size", &self.size)?;
        dict.set_item("timestamp", &self.timestamp)?;
        Ok(dict)
    }

    fn __repr__(&self) -> String {
        format!("RustMagnetInfo(name='{}', size='{}')", self.name, self.size)
    }
}

// ---------------------------------------------------------------------------
// MovieIndexEntry
// ---------------------------------------------------------------------------

#[pyclass(name = "RustMovieIndexEntry")]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MovieIndexEntry {
    #[pyo3(get, set)]
    pub href: String,
    #[pyo3(get, set)]
    pub video_code: String,
    #[pyo3(get, set)]
    pub title: String,
    #[pyo3(get, set)]
    pub rate: String,
    #[pyo3(get, set)]
    pub comment_count: String,
    #[pyo3(get, set)]
    pub release_date: String,
    #[pyo3(get, set)]
    pub tags: Vec<String>,
    #[pyo3(get, set)]
    pub cover_url: String,
    #[pyo3(get, set)]
    pub page: i32,
    #[pyo3(get, set)]
    pub ranking: Option<i32>,
}

#[pymethods]
impl MovieIndexEntry {
    #[new]
    #[pyo3(signature = (href, video_code, title=String::new(), rate=String::new(), comment_count=String::new(), release_date=String::new(), tags=vec![], cover_url=String::new(), page=1, ranking=None))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        href: String,
        video_code: String,
        title: String,
        rate: String,
        comment_count: String,
        release_date: String,
        tags: Vec<String>,
        cover_url: String,
        page: i32,
        ranking: Option<i32>,
    ) -> Self {
        Self {
            href,
            video_code,
            title,
            rate,
            comment_count,
            release_date,
            tags,
            cover_url,
            page,
            ranking,
        }
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = new_dict(py);
        dict.set_item("href", &self.href)?;
        dict.set_item("video_code", &self.video_code)?;
        dict.set_item("title", &self.title)?;
        dict.set_item("rate", &self.rate)?;
        dict.set_item("comment_count", &self.comment_count)?;
        dict.set_item("release_date", &self.release_date)?;
        dict.set_item("tags", &self.tags)?;
        dict.set_item("cover_url", &self.cover_url)?;
        dict.set_item("page", self.page)?;
        dict.set_item("ranking", self.ranking)?;
        Ok(dict)
    }

    fn to_legacy_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = new_dict(py);
        dict.set_item("href", &self.href)?;
        dict.set_item("video_code", &self.video_code)?;
        dict.set_item("page", self.page)?;
        dict.set_item("actor", "")?;
        dict.set_item("rate", &self.rate)?;
        dict.set_item("comment_number", &self.comment_count)?;
        Ok(dict)
    }

    fn __repr__(&self) -> String {
        format!(
            "RustMovieIndexEntry(video_code='{}', title='{}')",
            self.video_code, self.title
        )
    }
}

// ---------------------------------------------------------------------------
// MovieDetail
// ---------------------------------------------------------------------------

#[pyclass(name = "RustMovieDetail")]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MovieDetail {
    #[pyo3(get, set)]
    pub title: String,
    #[pyo3(get, set)]
    pub video_code: String,
    #[pyo3(get, set)]
    pub code_prefix_link: String,
    #[pyo3(get, set)]
    pub duration: String,
    #[pyo3(get, set)]
    pub release_date: String,
    #[pyo3(get, set)]
    pub publisher: Option<MovieLink>,
    #[pyo3(get, set)]
    pub maker: Option<MovieLink>,
    #[pyo3(get, set)]
    pub series: Option<MovieLink>,
    #[pyo3(get, set)]
    pub directors: Vec<MovieLink>,
    #[pyo3(get, set)]
    pub tags: Vec<MovieLink>,
    #[pyo3(get, set)]
    pub rate: String,
    #[pyo3(get, set)]
    pub comment_count: String,
    #[pyo3(get, set)]
    pub poster_url: String,
    #[pyo3(get, set)]
    pub fanart_urls: Vec<String>,
    #[pyo3(get, set)]
    pub trailer_url: Option<String>,
    #[pyo3(get, set)]
    pub actors: Vec<MovieLink>,
    #[pyo3(get, set)]
    pub magnets: Vec<MagnetInfo>,
    #[pyo3(get, set)]
    pub review_count: i32,
    #[pyo3(get, set)]
    pub want_count: i32,
    #[pyo3(get, set)]
    pub watched_count: i32,
    #[pyo3(get, set)]
    pub parse_success: bool,
}

impl Default for MovieDetail {
    fn default() -> Self {
        Self {
            title: String::new(),
            video_code: String::new(),
            code_prefix_link: String::new(),
            duration: String::new(),
            release_date: String::new(),
            publisher: None,
            maker: None,
            series: None,
            directors: Vec::new(),
            tags: Vec::new(),
            rate: String::new(),
            comment_count: String::new(),
            poster_url: String::new(),
            fanart_urls: Vec::new(),
            trailer_url: None,
            actors: Vec::new(),
            magnets: Vec::new(),
            review_count: 0,
            want_count: 0,
            watched_count: 0,
            parse_success: true,
        }
    }
}

#[pymethods]
impl MovieDetail {
    #[new]
    #[pyo3(signature = ())]
    fn new() -> Self {
        Self::default()
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = new_dict(py);
        dict.set_item("title", &self.title)?;
        dict.set_item("video_code", &self.video_code)?;
        dict.set_item("code_prefix_link", &self.code_prefix_link)?;
        dict.set_item("duration", &self.duration)?;
        dict.set_item("release_date", &self.release_date)?;

        let pub_dict = self.publisher.as_ref().map(|p| p.to_dict(py)).transpose()?;
        dict.set_item("publisher", pub_dict)?;

        let maker_dict = self.maker.as_ref().map(|m| m.to_dict(py)).transpose()?;
        dict.set_item("maker", maker_dict)?;

        let series_dict = self.series.as_ref().map(|s| s.to_dict(py)).transpose()?;
        dict.set_item("series", series_dict)?;

        let dirs: Vec<_> = self
            .directors
            .iter()
            .map(|d| d.to_dict(py))
            .collect::<Result<_, _>>()?;
        dict.set_item("directors", dirs)?;

        let tag_dicts: Vec<_> = self
            .tags
            .iter()
            .map(|t| t.to_dict(py))
            .collect::<Result<_, _>>()?;
        dict.set_item("tags", tag_dicts)?;

        dict.set_item("rate", &self.rate)?;
        dict.set_item("comment_count", &self.comment_count)?;
        dict.set_item("poster_url", &self.poster_url)?;
        dict.set_item("fanart_urls", &self.fanart_urls)?;
        dict.set_item("trailer_url", &self.trailer_url)?;

        let actor_dicts: Vec<_> = self
            .actors
            .iter()
            .map(|a| a.to_dict(py))
            .collect::<Result<_, _>>()?;
        dict.set_item("actors", actor_dicts)?;

        let magnet_dicts: Vec<_> = self
            .magnets
            .iter()
            .map(|m| m.to_dict(py))
            .collect::<Result<_, _>>()?;
        dict.set_item("magnets", magnet_dicts)?;

        dict.set_item("review_count", self.review_count)?;
        dict.set_item("want_count", self.want_count)?;
        dict.set_item("watched_count", self.watched_count)?;
        dict.set_item("parse_success", self.parse_success)?;
        Ok(dict)
    }

    fn get_first_actor_name(&self) -> String {
        self.actors.first().map_or(String::new(), |a| a.name.clone())
    }

    fn get_magnets_as_legacy<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyDict>>> {
        self.magnets.iter().map(|m| m.to_dict(py)).collect()
    }

    fn __repr__(&self) -> String {
        format!(
            "RustMovieDetail(video_code='{}', title='{}')",
            self.video_code, self.title
        )
    }
}

// ---------------------------------------------------------------------------
// IndexPageResult
// ---------------------------------------------------------------------------

#[pyclass(name = "RustIndexPageResult")]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct IndexPageResult {
    #[pyo3(get, set)]
    pub has_movie_list: bool,
    #[pyo3(get, set)]
    pub movies: Vec<MovieIndexEntry>,
    #[pyo3(get, set)]
    pub page_title: String,
}

impl Default for IndexPageResult {
    fn default() -> Self {
        Self {
            has_movie_list: false,
            movies: Vec::new(),
            page_title: String::new(),
        }
    }
}

#[pymethods]
impl IndexPageResult {
    #[new]
    #[pyo3(signature = (has_movie_list=false, movies=vec![], page_title=String::new()))]
    fn new(has_movie_list: bool, movies: Vec<MovieIndexEntry>, page_title: String) -> Self {
        Self {
            has_movie_list,
            movies,
            page_title,
        }
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = new_dict(py);
        dict.set_item("has_movie_list", self.has_movie_list)?;
        let movie_dicts: Vec<_> = self
            .movies
            .iter()
            .map(|m| m.to_dict(py))
            .collect::<Result<_, _>>()?;
        dict.set_item("movies", movie_dicts)?;
        dict.set_item("page_title", &self.page_title)?;
        Ok(dict)
    }
}

// ---------------------------------------------------------------------------
// CategoryPageResult
// ---------------------------------------------------------------------------

#[pyclass(name = "RustCategoryPageResult")]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CategoryPageResult {
    #[pyo3(get, set)]
    pub has_movie_list: bool,
    #[pyo3(get, set)]
    pub movies: Vec<MovieIndexEntry>,
    #[pyo3(get, set)]
    pub page_title: String,
    #[pyo3(get, set)]
    pub category_type: String,
    #[pyo3(get, set)]
    pub category_name: String,
}

#[pymethods]
impl CategoryPageResult {
    #[new]
    #[pyo3(signature = (has_movie_list=false, movies=vec![], page_title=String::new(), category_type=String::new(), category_name=String::new()))]
    fn new(
        has_movie_list: bool,
        movies: Vec<MovieIndexEntry>,
        page_title: String,
        category_type: String,
        category_name: String,
    ) -> Self {
        Self {
            has_movie_list,
            movies,
            page_title,
            category_type,
            category_name,
        }
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = new_dict(py);
        dict.set_item("has_movie_list", self.has_movie_list)?;
        let movie_dicts: Vec<_> = self
            .movies
            .iter()
            .map(|m| m.to_dict(py))
            .collect::<Result<_, _>>()?;
        dict.set_item("movies", movie_dicts)?;
        dict.set_item("page_title", &self.page_title)?;
        dict.set_item("category_type", &self.category_type)?;
        dict.set_item("category_name", &self.category_name)?;
        Ok(dict)
    }
}

// ---------------------------------------------------------------------------
// TopPageResult
// ---------------------------------------------------------------------------

#[pyclass(name = "RustTopPageResult")]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TopPageResult {
    #[pyo3(get, set)]
    pub has_movie_list: bool,
    #[pyo3(get, set)]
    pub movies: Vec<MovieIndexEntry>,
    #[pyo3(get, set)]
    pub page_title: String,
    #[pyo3(get, set)]
    pub top_type: String,
    #[pyo3(get, set)]
    pub period: Option<String>,
}

#[pymethods]
impl TopPageResult {
    #[new]
    #[pyo3(signature = (has_movie_list=false, movies=vec![], page_title=String::new(), top_type=String::new(), period=None))]
    fn new(
        has_movie_list: bool,
        movies: Vec<MovieIndexEntry>,
        page_title: String,
        top_type: String,
        period: Option<String>,
    ) -> Self {
        Self {
            has_movie_list,
            movies,
            page_title,
            top_type,
            period,
        }
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = new_dict(py);
        dict.set_item("has_movie_list", self.has_movie_list)?;
        let movie_dicts: Vec<_> = self
            .movies
            .iter()
            .map(|m| m.to_dict(py))
            .collect::<Result<_, _>>()?;
        dict.set_item("movies", movie_dicts)?;
        dict.set_item("page_title", &self.page_title)?;
        dict.set_item("top_type", &self.top_type)?;
        dict.set_item("period", &self.period)?;
        Ok(dict)
    }
}

// ---------------------------------------------------------------------------
// TagOption
// ---------------------------------------------------------------------------

#[pyclass(name = "RustTagOption")]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TagOption {
    #[pyo3(get, set)]
    pub name: String,
    #[pyo3(get, set)]
    pub tag_id: String,
    #[pyo3(get, set)]
    pub selected: bool,
}

#[pymethods]
impl TagOption {
    #[new]
    #[pyo3(signature = (name, tag_id=String::new(), selected=false))]
    fn new(name: String, tag_id: String, selected: bool) -> Self {
        Self {
            name,
            tag_id,
            selected,
        }
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = new_dict(py);
        dict.set_item("name", &self.name)?;
        dict.set_item("tag_id", &self.tag_id)?;
        dict.set_item("selected", self.selected)?;
        Ok(dict)
    }
}

// ---------------------------------------------------------------------------
// TagCategory
// ---------------------------------------------------------------------------

#[pyclass(name = "RustTagCategory")]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TagCategory {
    #[pyo3(get, set)]
    pub category_id: String,
    #[pyo3(get, set)]
    pub name: String,
    #[pyo3(get, set)]
    pub options: Vec<TagOption>,
}

#[pymethods]
impl TagCategory {
    #[new]
    #[pyo3(signature = (category_id, name, options=vec![]))]
    fn new(category_id: String, name: String, options: Vec<TagOption>) -> Self {
        Self {
            category_id,
            name,
            options,
        }
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = new_dict(py);
        dict.set_item("category_id", &self.category_id)?;
        dict.set_item("name", &self.name)?;
        let opt_dicts: Vec<_> = self
            .options
            .iter()
            .map(|o| o.to_dict(py))
            .collect::<Result<_, _>>()?;
        dict.set_item("options", opt_dicts)?;
        Ok(dict)
    }

    fn get_id_to_name_map(&self) -> HashMap<String, String> {
        self.options
            .iter()
            .filter(|o| !o.tag_id.is_empty())
            .map(|o| (o.tag_id.clone(), o.name.clone()))
            .collect()
    }

    fn get_name_to_id_map(&self) -> HashMap<String, String> {
        self.options
            .iter()
            .filter(|o| !o.tag_id.is_empty())
            .map(|o| (o.name.clone(), o.tag_id.clone()))
            .collect()
    }

    fn get_selected(&self) -> Vec<TagOption> {
        self.options
            .iter()
            .filter(|o| o.selected)
            .cloned()
            .collect()
    }
}

// ---------------------------------------------------------------------------
// TagPageResult
// ---------------------------------------------------------------------------

#[pyclass(name = "RustTagPageResult")]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TagPageResult {
    #[pyo3(get, set)]
    pub has_movie_list: bool,
    #[pyo3(get, set)]
    pub movies: Vec<MovieIndexEntry>,
    #[pyo3(get, set)]
    pub page_title: String,
    #[pyo3(get, set)]
    pub categories: Vec<TagCategory>,
    #[pyo3(get, set)]
    pub current_selections: HashMap<String, String>,
}

#[pymethods]
impl TagPageResult {
    #[new]
    #[pyo3(signature = (has_movie_list=false, movies=vec![], page_title=String::new(), categories=vec![], current_selections=HashMap::new()))]
    fn new(
        has_movie_list: bool,
        movies: Vec<MovieIndexEntry>,
        page_title: String,
        categories: Vec<TagCategory>,
        current_selections: HashMap<String, String>,
    ) -> Self {
        Self {
            has_movie_list,
            movies,
            page_title,
            categories,
            current_selections,
        }
    }

    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = new_dict(py);
        dict.set_item("has_movie_list", self.has_movie_list)?;
        let movie_dicts: Vec<_> = self
            .movies
            .iter()
            .map(|m| m.to_dict(py))
            .collect::<Result<_, _>>()?;
        dict.set_item("movies", movie_dicts)?;
        dict.set_item("page_title", &self.page_title)?;
        let cat_dicts: Vec<_> = self
            .categories
            .iter()
            .map(|c| c.to_dict(py))
            .collect::<Result<_, _>>()?;
        dict.set_item("categories", cat_dicts)?;
        dict.set_item("current_selections", &self.current_selections)?;
        Ok(dict)
    }

    fn get_category_by_id(&self, cid: &str) -> Option<TagCategory> {
        self.categories.iter().find(|c| c.category_id == cid).cloned()
    }

    fn get_category_by_name(&self, name: &str) -> Option<TagCategory> {
        self.categories.iter().find(|c| c.name == name).cloned()
    }

    fn get_full_id_to_name_map(&self) -> HashMap<String, HashMap<String, String>> {
        self.categories
            .iter()
            .map(|c| (c.category_id.clone(), c.get_id_to_name_map()))
            .collect()
    }
}
