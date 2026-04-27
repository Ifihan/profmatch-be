from __future__ import annotations

from dataclasses import dataclass

INELIGIBLE_TITLE_KEYWORDS = {"visiting", "adjunct", "emeritus", "honorary"}
ACADEMIC_TITLE_KEYWORDS = {
    "professor",
    "lecturer",
    "reader",
    "faculty",
    "chair",
    "dean",
    "research fellow",
    "research professor",
    "principal investigator",
}
NON_ACADEMIC_TITLE_KEYWORDS = {
    "administrator",
    "admissions",
    "advisor",
    "analyst",
    "assistant registrar",
    "career",
    "coordinator",
    "counselor",
    "developer",
    "director of operations",
    "finance",
    "hr",
    "it support",
    "librarian",
    "manager",
    "marketing",
    "officer",
    "program manager",
    "recruiter",
    "specialist",
    "staff",
    "student services",
    "technician",
}
TITLE_NORMALIZATION_RULES = (
    ("assistant professor", "Assistant Professor"),
    ("associate professor", "Associate Professor"),
    ("full professor", "Professor"),
    ("research professor", "Research Professor"),
    ("assistant lecturer", "Assistant Lecturer"),
    ("senior lecturer", "Senior Lecturer"),
    ("principal lecturer", "Principal Lecturer"),
    ("lecturer", "Lecturer"),
    ("reader", "Reader"),
    ("professor", "Professor"),
)
TITLE_PREFIXES = {
    "dr",
    "dr.",
    "prof",
    "prof.",
    "professor",
    "mr",
    "mr.",
    "mrs",
    "mrs.",
    "ms",
    "ms.",
}


@dataclass(frozen=True)
class AcademicUnitRule:
    """Routing rule for a department or academic unit family."""

    keywords: frozenset[str]
    synonyms: frozenset[str]


@dataclass(frozen=True)
class MatchingRouteRule:
    """Routing rule for a broad discipline."""

    keywords: frozenset[str]
    units: dict[str, AcademicUnitRule]
    discovery_terms: tuple[str, ...]


MATCHING_ROUTES: dict[str, MatchingRouteRule] = {
    "computing": MatchingRouteRule(
        keywords=frozenset({
            "ai",
            "artificial intelligence",
            "computer science",
            "computing",
            "cybersecurity",
            "data science",
            "distributed systems",
            "machine learning",
            "natural language processing",
            "nlp",
            "programming languages",
            "software engineering",
            "software systems",
        }),
        units={
            "computer science": AcademicUnitRule(
                keywords=frozenset({
                    "ai",
                    "algorithm",
                    "artificial intelligence",
                    "computer",
                    "computing",
                    "cybersecurity",
                    "data science",
                    "database",
                    "distributed systems",
                    "human computer interaction",
                    "machine learning",
                    "natural language processing",
                    "nlp",
                    "programming",
                    "software",
                    "software systems",
                    "systems",
                }),
                synonyms=frozenset({
                    "artificial intelligence",
                    "computer and information science",
                    "computer science",
                    "computer science and engineering",
                    "computing",
                    "computing science",
                    "informatics",
                    "information science",
                    "information systems",
                    "school of computing",
                    "school of computer science",
                }),
            ),
            "software engineering": AcademicUnitRule(
                keywords=frozenset({
                    "devops",
                    "program analysis",
                    "programming",
                    "requirements",
                    "software",
                    "software engineering",
                    "software systems",
                    "testing",
                    "verification",
                }),
                synonyms=frozenset({
                    "software engineering",
                    "software systems",
                }),
            ),
        },
        discovery_terms=("computer science", "computing science", "software engineering"),
    ),
    "engineering": MatchingRouteRule(
        keywords=frozenset({
            "aerospace",
            "biomedical engineering",
            "chemical engineering",
            "civil engineering",
            "electrical engineering",
            "engineering",
            "materials",
            "mechanical engineering",
            "robotics",
            "signal processing",
        }),
        units={
            "electrical engineering": AcademicUnitRule(
                keywords=frozenset({
                    "circuit",
                    "communication systems",
                    "control systems",
                    "electrical",
                    "electronics",
                    "power systems",
                    "signal processing",
                    "telecommunications",
                }),
                synonyms=frozenset({
                    "electrical and computer engineering",
                    "electrical and electronics engineering",
                    "electrical engineering",
                    "electronic engineering",
                    "electronics",
                }),
            ),
            "mechanical engineering": AcademicUnitRule(
                keywords=frozenset({
                    "cad",
                    "fluid mechanics",
                    "manufacturing",
                    "mechanical",
                    "robotics",
                    "thermodynamics",
                }),
                synonyms=frozenset({
                    "aerospace engineering",
                    "mechanical engineering",
                }),
            ),
            "civil engineering": AcademicUnitRule(
                keywords=frozenset({
                    "construction",
                    "geotechnical",
                    "hydrology",
                    "infrastructure",
                    "structural",
                    "transportation",
                }),
                synonyms=frozenset({
                    "civil engineering",
                    "construction engineering",
                }),
            ),
            "chemical engineering": AcademicUnitRule(
                keywords=frozenset({
                    "catalysis",
                    "chemical",
                    "process systems",
                    "reaction engineering",
                }),
                synonyms=frozenset({
                    "chemical engineering",
                    "process engineering",
                }),
            ),
        },
        discovery_terms=("engineering", "faculty of engineering"),
    ),
    "life_sciences_health": MatchingRouteRule(
        keywords=frozenset({
            "biology",
            "biomedical",
            "genetics",
            "health",
            "medical",
            "medicine",
            "microbiology",
            "molecular biology",
            "neuroscience",
            "nursing",
            "public health",
        }),
        units={
            "biology": AcademicUnitRule(
                keywords=frozenset({
                    "bioinformatics",
                    "biology",
                    "ecology",
                    "genetics",
                    "microbiology",
                    "molecular biology",
                    "neuroscience",
                }),
                synonyms=frozenset({
                    "biological sciences",
                    "biology",
                    "life sciences",
                    "molecular biology",
                }),
            ),
            "medicine": AcademicUnitRule(
                keywords=frozenset({
                    "clinical",
                    "medicine",
                    "pathology",
                    "surgery",
                    "translational",
                }),
                synonyms=frozenset({
                    "faculty of medicine",
                    "medical sciences",
                    "medicine",
                    "school of medicine",
                }),
            ),
            "public health": AcademicUnitRule(
                keywords=frozenset({
                    "epidemiology",
                    "global health",
                    "health policy",
                    "population health",
                    "public health",
                }),
                synonyms=frozenset({
                    "community health",
                    "public health",
                    "school of public health",
                }),
            ),
            "nursing": AcademicUnitRule(
                keywords=frozenset({
                    "midwifery",
                    "nursing",
                    "patient care",
                }),
                synonyms=frozenset({
                    "nursing",
                    "school of nursing",
                }),
            ),
        },
        discovery_terms=("life sciences", "medicine", "public health"),
    ),
    "physical_sciences_math": MatchingRouteRule(
        keywords=frozenset({
            "astronomy",
            "chemistry",
            "mathematics",
            "physics",
            "statistics",
        }),
        units={
            "mathematics": AcademicUnitRule(
                keywords=frozenset({
                    "algebra",
                    "analysis",
                    "applied mathematics",
                    "geometry",
                    "math",
                    "mathematics",
                    "statistics",
                }),
                synonyms=frozenset({
                    "mathematics",
                    "school of mathematics",
                    "statistics",
                }),
            ),
            "physics": AcademicUnitRule(
                keywords=frozenset({
                    "astrophysics",
                    "condensed matter",
                    "particle physics",
                    "physics",
                    "quantum",
                }),
                synonyms=frozenset({
                    "astronomy",
                    "physics",
                }),
            ),
            "chemistry": AcademicUnitRule(
                keywords=frozenset({
                    "analytical chemistry",
                    "biochemistry",
                    "chemistry",
                    "organic chemistry",
                }),
                synonyms=frozenset({
                    "chemistry",
                    "chemical sciences",
                }),
            ),
        },
        discovery_terms=("mathematics", "physics", "chemistry"),
    ),
    "business_economics": MatchingRouteRule(
        keywords=frozenset({
            "accounting",
            "business",
            "economics",
            "entrepreneurship",
            "finance",
            "management",
            "marketing",
            "operations",
            "supply chain",
        }),
        units={
            "economics": AcademicUnitRule(
                keywords=frozenset({
                    "behavioral economics",
                    "development economics",
                    "econometrics",
                    "economics",
                    "labor economics",
                    "macroeconomics",
                }),
                synonyms=frozenset({
                    "economics",
                    "school of economics",
                }),
            ),
            "finance": AcademicUnitRule(
                keywords=frozenset({
                    "asset pricing",
                    "banking",
                    "corporate finance",
                    "finance",
                    "financial economics",
                }),
                synonyms=frozenset({
                    "banking and finance",
                    "finance",
                }),
            ),
            "management": AcademicUnitRule(
                keywords=frozenset({
                    "innovation",
                    "leadership",
                    "management",
                    "operations management",
                    "organization",
                    "strategy",
                }),
                synonyms=frozenset({
                    "business administration",
                    "management",
                    "school of business",
                }),
            ),
            "marketing": AcademicUnitRule(
                keywords=frozenset({
                    "consumer behavior",
                    "digital marketing",
                    "marketing",
                    "pricing",
                }),
                synonyms=frozenset({
                    "marketing",
                }),
            ),
        },
        discovery_terms=("business", "economics", "management"),
    ),
    "social_sciences": MatchingRouteRule(
        keywords=frozenset({
            "anthropology",
            "communication",
            "development studies",
            "geography",
            "political science",
            "psychology",
            "public administration",
            "sociology",
        }),
        units={
            "psychology": AcademicUnitRule(
                keywords=frozenset({
                    "cognitive psychology",
                    "developmental psychology",
                    "psychology",
                    "social psychology",
                }),
                synonyms=frozenset({
                    "psychology",
                }),
            ),
            "sociology": AcademicUnitRule(
                keywords=frozenset({
                    "demography",
                    "inequality",
                    "social theory",
                    "sociology",
                }),
                synonyms=frozenset({
                    "sociology",
                }),
            ),
            "political science": AcademicUnitRule(
                keywords=frozenset({
                    "comparative politics",
                    "governance",
                    "international relations",
                    "political science",
                    "public policy",
                }),
                synonyms=frozenset({
                    "government",
                    "political science",
                    "politics",
                }),
            ),
            "anthropology": AcademicUnitRule(
                keywords=frozenset({
                    "anthropology",
                    "culture",
                    "ethnography",
                }),
                synonyms=frozenset({
                    "anthropology",
                }),
            ),
        },
        discovery_terms=("social sciences", "psychology", "political science"),
    ),
    "humanities": MatchingRouteRule(
        keywords=frozenset({
            "classics",
            "history",
            "languages",
            "linguistics",
            "literature",
            "philosophy",
            "religion",
            "theology",
        }),
        units={
            "history": AcademicUnitRule(
                keywords=frozenset({
                    "archival",
                    "historiography",
                    "history",
                    "medieval",
                    "modern history",
                }),
                synonyms=frozenset({
                    "history",
                }),
            ),
            "philosophy": AcademicUnitRule(
                keywords=frozenset({
                    "ethics",
                    "logic",
                    "metaphysics",
                    "philosophy",
                }),
                synonyms=frozenset({
                    "philosophy",
                }),
            ),
            "literature": AcademicUnitRule(
                keywords=frozenset({
                    "comparative literature",
                    "literary studies",
                    "literature",
                    "poetry",
                }),
                synonyms=frozenset({
                    "comparative literature",
                    "english",
                    "literature",
                }),
            ),
            "linguistics": AcademicUnitRule(
                keywords=frozenset({
                    "linguistics",
                    "phonology",
                    "semantics",
                    "syntax",
                }),
                synonyms=frozenset({
                    "linguistics",
                    "modern languages",
                }),
            ),
        },
        discovery_terms=("humanities", "history", "literature"),
    ),
    "law_policy_education": MatchingRouteRule(
        keywords=frozenset({
            "curriculum",
            "education",
            "law",
            "learning sciences",
            "legal",
            "pedagogy",
            "policy",
            "teaching",
        }),
        units={
            "law": AcademicUnitRule(
                keywords=frozenset({
                    "constitutional law",
                    "human rights",
                    "law",
                    "legal studies",
                }),
                synonyms=frozenset({
                    "faculty of law",
                    "law",
                    "school of law",
                }),
            ),
            "education": AcademicUnitRule(
                keywords=frozenset({
                    "curriculum",
                    "education",
                    "learning sciences",
                    "pedagogy",
                    "teacher education",
                }),
                synonyms=frozenset({
                    "education",
                    "faculty of education",
                    "school of education",
                }),
            ),
            "public policy": AcademicUnitRule(
                keywords=frozenset({
                    "governance",
                    "policy",
                    "public administration",
                    "public policy",
                }),
                synonyms=frozenset({
                    "policy studies",
                    "public administration",
                    "public policy",
                }),
            ),
        },
        discovery_terms=("law", "education", "public policy"),
    ),
}
